"""Preliminary pitch sequence alignment: note groups + DP edit distance.

Dependencies: python -m pip install mido python-rtmidi music21 matplotlib
Run: python test_accuracy_with_chart.py
Prints alignment results and exports PNG charts to reports/.
Each chart displays at most 30 positions; additional positions continue on new pages.
"""

import math
import time
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido
from music21 import converter, stream


SCORE_FILE = Path(__file__).with_name("Piano.musicxml")
MIDI_PORT = "Roland Digital Piano 0"

# None: all parts; [1]: first part; [1, 2]: first two parts.
# Part numbers do not necessarily map to hands; all voices/staves in each part are read.
PARTS = None
CHORD_WINDOW = 0.05
# Limit chart display positions, not alignment groups or the recorded note count.
CHART_POSITIONS_PER_PAGE = 30

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SOLFEGE = ("Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si")

# Traceback operation codes also define the priority for exact ties.
MATCH, SKIP_SCORE, SKIP_PLAYED = 1, 2, 3


@dataclass
class ScoreGroup:
    offset: Fraction
    notes: list
    locations: list


@dataclass
class PlayedGroup:
    # Each event is (seconds since recording started, MIDI pitch, velocity).
    events: list

    @property
    def start(self):
        return self.events[0][0]

    @property
    def notes(self):
        return [pitch for _, pitch, _ in self.events]


@dataclass(frozen=True)
class Counts:
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    extra: int = 0

    def __add__(self, other):
        return Counts(
            self.correct + other.correct,
            self.wrong + other.wrong,
            self.missed + other.missed,
            self.extra + other.extra,
        )

    @property
    def cost(self):
        return self.wrong + self.missed + self.extra


@dataclass
class Comparison:
    counts: Counts
    unmatched_expected: list
    unmatched_played: list


@dataclass
class AlignmentRow:
    # Indices are zero-based internally and printed as one-based group numbers.
    score_index: object
    played_index: object
    comparison: Comparison


def note_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def format_notes(notes):
    # Sort for display only; Counter comparisons preserve duplicate pitch counts.
    return "[" + ", ".join(f"{note}({note_name(note)})" for note in sorted(notes)) + "]"


def quarter_position(value):
    # music21 positions use quarter-note units; accept both float and Fraction.
    return Fraction(value).limit_denominator(1_000_000)


def read_reference(filename):
    score = converter.parse(str(filename))
    parts = list(score.parts)
    print("Score parts:")
    for number, part in enumerate(parts, 1):
        print(f"  {number}: {part.partName or part.id}")

    selected = list(range(1, len(parts) + 1)) if PARTS is None else list(PARTS)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("PARTS must contain unique part numbers and must not be empty.")
    if any(not isinstance(number, int) or not 1 <= number <= len(parts) for number in selected):
        raise ValueError(f"PARTS numbers must be between 1 and {len(parts)}.")

    groups = {}
    for part_number in selected:
        part = parts[part_number - 1]
        for element in part.recurse().notes:
            # Preserve the original grace-note exclusion; .notes already excludes rests.
            if element.duration.isGrace:
                continue
            members = element.notes if element.isChord else [element]
            pitches = [
                int(note.pitch.midi)
                for note in members
                if not (note.tie and note.tie.type in ("continue", "stop"))
            ]
            if not pitches:
                continue

            offset = quarter_position(element.getOffsetInHierarchy(part))
            measure = element.getContextByClass(stream.Measure)
            if measure is None:
                location = f"part={part_number}, measure=unknown, measure_offset=unknown"
            else:
                number = f"{measure.number}{getattr(measure, 'numberSuffix', '') or ''}"
                local_offset = quarter_position(element.getOffsetInHierarchy(measure))
                location = f"part={part_number}, measure={number}, measure_offset={local_offset}"

            group = groups.setdefault(offset, ScoreGroup(offset, [], []))
            group.notes.extend(pitches)
            if location not in group.locations:
                group.locations.append(location)

    return [groups[offset] for offset in sorted(groups)]


def starts_new_group(events, onset, window):
    timestamp, pitch, _ = onset
    return (
        not events
        or timestamp - events[0][0] > window
        or any(previous_pitch == pitch for _, previous_pitch, _ in events)
    )


def group_performance(onsets, window=CHORD_WINDOW):
    groups = []
    for onset in onsets:
        if not groups or starts_new_group(groups[-1].events, onset, window):
            groups.append(PlayedGroup([]))
        groups[-1].events.append(onset)
    return groups


def print_live_group(events):
    details = " | ".join(
        f"note=({pitch}, {note_name(pitch)}, {SOLFEGE[pitch % 12]}) | velocity={velocity}"
        for _, pitch, velocity in events
    )
    print(f"ON | {events[0][0]:.3f}s | {details}")


def record_midi():
    onsets = []
    pending = []
    port = None
    try:
        port = mido.open_input(MIDI_PORT)
        input("Press Enter to start recording, then Ctrl+C to stop after playing.")
        # Discard MIDI messages received before recording starts.
        list(port.iter_pending())
        start = time.perf_counter()
        print("Recording started...")
        while True:
            for msg in port.iter_pending():
                # Neither note_off nor note_on with velocity=0 is a new onset.
                if msg.type != "note_on" or msg.velocity <= 0:
                    continue
                onset = (time.perf_counter() - start, msg.note, msg.velocity)
                onsets.append(onset)
                if pending and starts_new_group(pending, onset, CHORD_WINDOW):
                    print_live_group(pending)
                    pending.clear()
                pending.append(onset)

            if pending and time.perf_counter() - start - pending[0][0] > CHORD_WINDOW:
                print_live_group(pending)
                pending.clear()
            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\nRecording stopped.")
    except Exception as error:
        print(f"\nMIDI recording error: {type(error).__name__}: {error}; analyzing notes received so far.")
    finally:
        # Close explicitly and catch errors separately so analysis can still run.
        if port is not None:
            try:
                port.close()
            except (Exception, KeyboardInterrupt) as error:
                print(f"MIDI port close error: {type(error).__name__}: {error}; continuing analysis.")
        if pending:
            print_live_group(pending)
    return onsets


def compare_group(expected, played):
    expected = Counter(expected)
    played = Counter(played)
    correct = sum((expected & played).values())
    unmatched_expected = sorted((expected - played).elements())
    unmatched_played = sorted((played - expected).elements())
    missing, added = len(unmatched_expected), len(unmatched_played)
    wrong = min(missing, added)
    return Comparison(
        Counts(correct, wrong, missing - wrong, added - wrong),
        unmatched_expected,
        unmatched_played,
    )


def align_groups(reference, performed):
    """Minimize note errors and trace back the full path without using timing."""
    n, m = len(reference), len(performed)
    traceback = [bytearray(m + 1) for _ in range(n + 1)]
    previous = [Counts()]
    for j, played in enumerate(performed, 1):
        previous.append(previous[-1] + Counts(extra=len(played.notes)))
        traceback[0][j] = SKIP_PLAYED

    for i, expected in enumerate(reference, 1):
        skip_score = Counts(missed=len(expected.notes))
        current = [previous[0] + skip_score]
        traceback[i][0] = SKIP_SCORE
        for j, played in enumerate(performed, 1):
            candidates = [
                (previous[j - 1] + compare_group(expected.notes, played.notes).counts, MATCH),
                (previous[j] + skip_score, SKIP_SCORE),
                (current[j - 1] + Counts(extra=len(played.notes)), SKIP_PLAYED),
            ]
            counts, operation = min(
                candidates,
                key=lambda item: (item[0].cost, -item[0].correct, item[1]),
            )
            current.append(counts)
            traceback[i][j] = operation
        previous = current

    total = previous[-1]
    path = []
    i, j = n, m
    while i or j:
        operation = traceback[i][j]
        if operation == MATCH:
            i -= 1
            j -= 1
            path.append(AlignmentRow(i, j, compare_group(reference[i].notes, performed[j].notes)))
        elif operation == SKIP_SCORE:
            i -= 1
            path.append(AlignmentRow(i, None, compare_group(reference[i].notes, [])))
        elif operation == SKIP_PLAYED:
            j -= 1
            path.append(AlignmentRow(None, j, compare_group([], performed[j].notes)))
        else:
            raise RuntimeError(f"Invalid traceback position: ({i}, {j})")
    path.reverse()
    return total, path


def print_alignment(reference, performed, total, path):
    print("\nFinal alignment path (offsets use quarter-note units; measure offsets start at 0):")
    print("Exact tie priority: match groups > skip score group > skip played group.")
    for number, row in enumerate(path, 1):
        expected = reference[row.score_index] if row.score_index is not None else None
        played = performed[row.played_index] if row.played_index is not None else None
        print(f"\n[{number}]")
        if expected is None:
            print("  score_group=None | measure=None | measure_offset=None | score_offset=None")
        else:
            print(
                f"  score_group={row.score_index + 1} | "
                f"{' ; '.join(expected.locations)} | score_offset={expected.offset}"
            )
        if played is None:
            print("  played_group=None | onset=None")
        else:
            print(f"  played_group={row.played_index + 1} | onset={played.start:.3f}s")
        print(f"  Expected notes: {format_notes(expected.notes if expected is not None else [])}")
        print(f"  Played notes: {format_notes(played.notes if played is not None else [])}")
        comparison = row.comparison
        counts = comparison.counts
        print(
            f"  correct={counts.correct}, wrong={counts.wrong}, "
            f"missed={counts.missed}, extra={counts.extra}"
        )
        print(
            f"  Unmatched expected notes ({len(comparison.unmatched_expected)}): "
            f"{format_notes(comparison.unmatched_expected)}"
        )
        print(
            f"  Unmatched played notes ({len(comparison.unmatched_played)}): "
            f"{format_notes(comparison.unmatched_played)}"
        )

    expected_count = sum(len(group.notes) for group in reference)
    played_count = sum(len(group.notes) for group in performed)
    note_accuracy = total.correct / expected_count if expected_count else None
    extra_note_ratio = total.extra / played_count if played_count else None
    print("\nResult:")
    print(f"Total score notes: {expected_count}")
    print(f"Total played notes: {played_count}")
    print(f"correct: {total.correct}")
    print(f"wrong: {total.wrong}")
    print(f"missed: {total.missed}")
    print(f"extra: {total.extra}")
    print(f"note_accuracy: {note_accuracy}")
    print(f"extra_note_ratio: {extra_note_ratio}")



def chart_note_slots(reference, performed, path):
    """Create plotting slots without inventing note-level substitutions."""
    slots = []
    for row in path:
        expected = reference[row.score_index] if row.score_index is not None else None
        played = performed[row.played_index] if row.played_index is not None else None
        ec = Counter(expected.notes if expected else [])
        pc = Counter(played.notes if played else [])
        common = ec & pc
        ae, ap = sorted((ec-pc).elements()), sorted((pc-ec).elements())
        group = f"S{row.score_index+1 if expected else '-'} / P{row.played_index+1 if played else '-'}"
        for pitch in sorted(common.elements()):
            slots.append((pitch, pitch, 'correct', group))
        if len(ae) == 1 and len(ap) == 1:
            # A unique residual pair within this aligned group.
            slots.append((ae[0], ap[0], 'wrong', group))
        elif ae and ap:
            # Counts may imply substitutions, but individual identities are ambiguous.
            for pitch in ae:
                slots.append((pitch, None, 'unresolved', group))
            for pitch in ap:
                slots.append((None, pitch, 'unresolved', group))
        else:
            for pitch in ae:
                slots.append((pitch, None, 'missed', group))
            for pitch in ap:
                slots.append((None, pitch, 'extra', group))
    return slots


def export_comparison_chart(reference, performed, total, path,
                            output_dir=None, title=None, illustrative=False):
    """Export paired thin pitch bars with at most CHART_POSITIONS_PER_PAGE positions per PNG."""
    from datetime import datetime
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D

    slots = chart_note_slots(reference, performed, path)
    expected_n = sum(len(g.notes) for g in reference)
    played_n = sum(len(g.notes) for g in performed)
    accuracy = total.correct / expected_n if expected_n else None
    extra_ratio = total.extra / played_n if played_n else None
    pages = [
        slots[i:i + CHART_POSITIONS_PER_PAGE]
        for i in range(0, len(slots), CHART_POSITIONS_PER_PAGE)
    ] or [[]]
    directory = Path(output_dir) if output_dir else Path(__file__).resolve().parent / 'reports'
    directory.mkdir(parents=True, exist_ok=True)
    run = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    colors = {'expected':'#4195df', 'correct':'#249a82', 'wrong':'#df902a',
              'extra':'#946cce', 'missed':'#d05270', 'unresolved':'#df902a'}
    all_pitches = [v for s in slots for v in s[:2] if v is not None]
    top = min(140, max(all_pitches, default=72)+15)
    has_unresolved = any(s[2]=='unresolved' for s in slots)
    files = []
    for page_no, page in enumerate(pages,1):
        fig, ax = plt.subplots(figsize=(16,8.2), dpi=180)
        fig.patch.set_facecolor('white')
        heading = title or f'{SCORE_FILE.stem} | expected vs. played notes'
        if illustrative:
            heading = 'ILLUSTRATIVE EXAMPLE | expected vs. played notes'
        fig.text(.075,.947,heading,fontsize=20,weight='bold',color='#23394f')
        acc = f'{accuracy:.2%}' if accuracy is not None else 'N/A'
        er = f'{extra_ratio:.2%}' if extra_ratio is not None else 'N/A'
        fig.text(.075,.898,
                 f'{expected_n} expected  |  {played_n} played  |  {total.correct} correct  |  '
                 f'{total.wrong} wrong  |  {total.missed} missed  |  {total.extra} extra',
                 fontsize=12,color='#40556c')
        fig.text(.075,.859,f'Note accuracy: {acc}    Extra-note ratio: {er}    '
                 f'Page {page_no}/{len(pages)}',fontsize=12,color='#40556c')
        handles = [Patch(color=colors[k],label=l) for k,l in [
            ('expected','Expected'),('correct','Played: correct'),
            ('wrong','Played: wrong'),('extra','Played: extra')]]
        handles.append(Line2D([],[],marker='x',color=colors['missed'],linestyle='None',label='Missed'))
        if has_unresolved:
            handles.append(Line2D([],[],marker='o',markerfacecolor='none',color=colors['wrong'],
                                  linestyle='None',label='Unpaired residual pitch'))
        fig.legend(handles=handles,loc='upper left',bbox_to_anchor=(.07,.829),
                   ncol=6,frameon=False,fontsize=9)
        xticks=[]; ticklabels=[]
        for i,(a,b,kind,group) in enumerate(page,1):
            for pitch, pos, color in [(a,i-.12,colors['expected']),
                                       (b,i+.12,colors.get(kind,colors['wrong']))]:
                if pitch is not None:
                    ax.bar(pos,pitch,width=.13,color=color,zorder=3)
                    ax.text(pos,pitch+1.8,note_name(pitch),ha='center',va='bottom',
                            rotation=90,fontsize=8.5,color=color)
            if kind=='missed':
                ax.scatter(i+.12,a,marker='x',s=65,color=colors['missed'],zorder=5)
            if kind=='wrong':
                ax.plot([i-.12,i+.12],[a,b],color=colors['wrong'],lw=1.4,zorder=4)
            if kind=='unresolved':
                pitch = a if a is not None else b
                pos = i-.12 if a is not None else i+.12
                ax.scatter(pos,pitch,marker='o',s=60,facecolors='none',
                           edgecolors=colors['wrong'],lw=1.5,zorder=5)
            if kind=='extra':
                ax.text(i-.12,b-3,'NO SCORE',ha='center',va='top',rotation=90,
                        fontsize=7,color='#8793a2')
            xticks.append(i)
            ticklabels.append(f'{(page_no-1)*CHART_POSITIONS_PER_PAGE+i}\n{group}')
            if i>1 and group!=page[i-2][3]:
                ax.axvline(i-.5,color='#edf0f4',lw=.7,zorder=0)
        if not page:
            ax.text(.5,.5,'No note events',transform=ax.transAxes,ha='center')
        ax.set_xticks(xticks,ticklabels,fontsize=7)
        ax.set_xlim(.4,max(len(page),1)+.6)
        ax.set_ylim(0,top)
        ticks=sorted(set([0]+[p for p in range(24,int(top),12)]))
        ax.set_yticks(ticks,[f'{note_name(p)} / {p}' for p in ticks])
        ax.set_ylabel('Pitch (MIDI note number)',labelpad=10)
        ax.set_xlabel('Aligned display position | left: expected / right: played | S: score group, P: played group',labelpad=12)
        ax.grid(axis='y',color='#e9eef3',zorder=0)
        ax.spines[['top','right']].set_visible(False)
        ax.spines[['bottom','left']].set_color('#d5dce5')
        ax.tick_params(colors='#526173')
        footer = ('Bar tops show pitch; widths do not represent duration. '
                  f'Pitch-only DP alignment; onset grouping = {CHORD_WINDOW*1000:g} ms. No rhythm score.')
        fig.text(.075,.055,footer,fontsize=9,color='#738296')
        if has_unresolved:
            fig.text(.075,.027,'Open orange circles: unmatched pitches in a group with ambiguous substitutions; '
                     'no individual wrong/missed/extra pairing is asserted.',fontsize=9,color='#9d650d')
        else:
            fig.text(.075,.027,'Counts and note accuracy describe the entire recording, not only this page. '
                     'Chord members share S/P group labels.',fontsize=9,color='#738296')
        fig.subplots_adjust(left=.08,right=.975,top=.75,bottom=.18)
        target = directory / f'alignment_{run}_page{page_no:02d}.png'
        fig.savefig(target,facecolor='white')
        plt.close(fig)
        files.append(target)
        print(f'Chart saved: {target}')
    return files


def main():
    print("Preliminary pitch sequence alignment; no rhythm scoring, timing model, or HMM.")
    print(f"CHORD_WINDOW={CHORD_WINDOW}s; fixed windows may affect scores for uneven chords and fast melodies.")
    print("Notes within a chord are unordered; unmatched notes are listed without assigning substitutions.")
    print("Score rules: exclude rests, count only the first tied onset, and exclude grace notes as before.")
    if not math.isfinite(CHORD_WINDOW) or CHORD_WINDOW < 0:
        raise ValueError("CHORD_WINDOW must be a finite, nonnegative number.")
    reference = read_reference(SCORE_FILE)
    if not reference:
        raise ValueError("No score notes found; check the score file and selected parts.")
    print(f"Score groups: {len(reference)}, total score notes: {sum(len(group.notes) for group in reference)}")
    onsets = record_midi()
    performed = group_performance(onsets, CHORD_WINDOW)
    total, path = align_groups(reference, performed)
    print_alignment(reference, performed, total, path)
    export_comparison_chart(reference, performed, total, path)


if __name__ == "__main__":
    main()
