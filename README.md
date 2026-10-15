# TestMidi

Small Python tools for inspecting live MIDI input and comparing a piano performance with a MusicXML score. The comparison is a preliminary pitch sequence alignment using note groups and dynamic programming edit distance.

All script messages and comments are in English.

## Folder contents

| File or directory | Purpose |
| --- | --- |
| `Piano.musicxml` | Reference score used by both accuracy scripts. |
| `test_midi.py` | Inspect live note-on/note-off messages, velocity, and held-note duration. |
| `test_accuracy_alignment.py` | Record MIDI, align it with the score, and print detailed results to the terminal. Does not export reports. |
| `test_accuracy_with_chart.py` | Run the same comparison and also export paginated PNG charts. |
| `reports/` | Exported chart images. Existing images are kept when a new session is exported. |
| `.vscode/settings.json` | VS Code Python environment/package manager preferences for Conda. This does not activate an environment in every terminal. |
| `README.md` | Setup, usage, configuration, and interpretation guide. |

## Setup

Connect the digital piano or MIDI keyboard and activate the Python environment you want to use. Install packages with that same Python interpreter:

```powershell
python -m pip install mido python-rtmidi music21 matplotlib
```

`matplotlib` is only needed for the chart script. `python-rtmidi` supplies a MIDI backend for `mido`.

List the available MIDI input names:

```powershell
python -c "import mido; print(mido.get_input_names())"
```

Set `MIDI_PORT` in the accuracy script you will run to an exact name from that list. The current setting is `"Roland Digital Piano 0"`. In `test_midi.py`, change the name passed directly to `mido.open_input(...)`.

If `python` in your terminal selects another environment, call the intended interpreter explicitly. For the existing Miniconda installation on this computer:

```powershell
& C:\Users\86138\miniconda3\python.exe .\test_accuracy_with_chart.py
```

## Run

Run commands from this folder, using one script at a time so they do not compete for the MIDI port.

### Inspect MIDI messages

```powershell
python test_midi.py
```

Recording starts immediately. Press Ctrl+C to stop. The output includes note names, velocity, and the duration from note-on to note-off. A note-on with velocity zero is treated as note-off. This script does not read the score or calculate accuracy.

### Compare with terminal output

```powershell
python test_accuracy_alignment.py
```

Press Enter to begin recording, play the score, then press Ctrl+C to stop. Messages received before recording begins are discarded. During recording, chord members are printed on one line with MIDI numbers, note names, solfege, and individual velocities.

After recording stops, the script prints the complete alignment path and overall statistics. MIDI recording and port-close exceptions are caught so analysis of the notes received so far can continue. If no notes were recorded, all score notes are counted as missed.

### Compare and export charts

```powershell
python test_accuracy_with_chart.py
```

The recording controls and terminal report are the same as above. After analysis, charts are saved under `reports/`, relative to the script directory:

```text
reports/alignment_<timestamp>_page01.png
reports/alignment_<timestamp>_page02.png
...
```

Each PNG contains **at most 30 aligned display positions**. Longer results continue on additional pages; no positions are discarded. For example, 61 positions produce pages containing 30, 30, and 1 position. Position numbers continue across pages. An empty result produces one chart marked `No note events`.

Summary counts and percentages on every page describe the **entire performance**, not just the current page. Changing the page size affects chart layout only. Previously exported images are not regenerated automatically.

## Configuration

The two accuracy scripts are independent copies. Edit the settings in the script you intend to run, or update both if they should use the same settings.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SCORE_FILE` | `Piano.musicxml` beside the script | Reference MusicXML file. |
| `MIDI_PORT` | `"Roland Digital Piano 0"` | Exact MIDI input port name. |
| `PARTS` | `None` | All score parts. Use `[1]` for the first part or `[1, 2]` for the first two. Part numbers start at 1. |
| `CHORD_WINDOW` | `0.05` seconds | Maximum onset span within a performance group, measured from its first note. Must be finite and nonnegative. |
| `CHART_POSITIONS_PER_PAGE` | `30` | Maximum display positions per PNG, in `test_accuracy_with_chart.py`. Use a positive integer. |

Part numbers do not necessarily correspond to left and right hands. All voices and staves inside a selected part are read.

## Grouping and alignment

### Score groups

Notes at the same score onset are combined into a single group, including simultaneous notes from different selected parts. Rests and grace notes are excluded. Each chord member counts as an individual note. For tied notes, only the initial onset is counted; tie continuations and stops are excluded independently for each chord member.

Each group retains its part/measure locations and its offset from the beginning of the score. The score is read in its stored order; the scripts do not expand repeat markings into a performance roadmap.

### Performance groups

Only note-on messages with positive velocity count as new onsets. Note-off messages and note-on messages with velocity zero do not add notes to the comparison.

A new group begins when the next onset is more than `CHORD_WINDOW` seconds after the first onset of the current group, or when a pitch already in that group is pressed again. The time window is measured from the first onset, not from the previous onset. An onset exactly at the window boundary remains in the group unless its pitch repeats.

For example, different pitches arriving at `0.000`, `0.030`, and `0.060` seconds form two groups with a 50 ms window: the first two notes together, then the third note.

### Dynamic programming

Alignment preserves group order and allows three operations:

| Operation | Contribution |
| --- | --- |
| Match one score group with one performance group | Compare their pitch multisets using `Counter`. |
| Skip a score group | Every note in the group counts as `missed`. |
| Skip a performance group | Every note in the group counts as `extra`. |

Within a matched pair of groups, note order is ignored and duplicate pitch counts are preserved:

```text
correct = number of matching MIDI pitches, including multiplicities
missing = number of remaining expected notes
added   = number of remaining played notes
wrong   = min(missing, added)
missed  = missing - wrong
extra   = added - wrong
```

The algorithm minimizes total `wrong + missed + extra`. Equal-cost paths prefer more `correct` notes. If both values are equal, the fixed local operation priority is match groups, skip score group, then skip performance group. The saved traceback produces the final path printed after recording stops.

The algorithm matches one group to one group. It does not merge or split groups during alignment. It uses no timing model, rhythm score, duration score, or HMM. Velocity is displayed but does not affect accuracy.

## Reading the terminal report

Each alignment row includes score and played group numbers, score locations, performance onset time, expected/played pitch lists, and error counts. A missing group is shown as `None`; its note list is `[]`.

| Field | Meaning |
| --- | --- |
| `part` | Selected score part number, starting at 1. |
| `measure` | Measure number stored in the score, including any suffix. |
| `measure_offset` | Position from the beginning of that measure, starting at 0. |
| `score_offset` | Position from the beginning of the score, starting at 0. |
| `onset` | Seconds since recording began, using the first note in the played group. |
| `60(C4)` | MIDI pitch 60, also displayed as note name C4. |

Both score offsets use **quarter-note lengths**, not seconds. In a regular 4/4 score without a pickup, measure 2 at its second quarter-note beat has `measure_offset=1` and `score_offset=5`. A value of `1/2` is an eighth-note length. These values are offsets, not universal beat numbers for every time signature.

Unmatched expected and played notes are printed separately with their counts. When several pitches are unmatched, the report does not assign a particular played note as the replacement for a particular expected note.

Final statistics are:

```text
note_accuracy    = correct / total score notes
extra_note_ratio = extra / total played notes
```

When no notes were played, `extra_note_ratio` is `None`. An empty reference score is rejected before recording starts. Extra notes are reported separately from `note_accuracy`.

## Reading the charts

Each horizontal position is a **display slot**, not necessarily one alignment group. A chord can occupy several slots, and ambiguous residual pitches can create separate expected-only and played-only slots. The 30-position limit applies to these slots. A group's slots may continue on the next page.

The labels `S1 / P1` mean score group 1 and played group 1. A dash means that side has no group. These labels preserve group identity even when its members occupy multiple positions.

| Mark | Meaning |
| --- | --- |
| Blue bar on the left | Expected pitch. |
| Green bar on the right | Correct played pitch. |
| Orange bar and connecting line | A unique unmatched expected/played pitch pair within the aligned group. |
| Purple bar | Extra played pitch with no expected counterpart. |
| Red cross | Missed expected pitch. |
| Open orange circle | An unmatched residual pitch in a group where individual substitutions are ambiguous. |

For multiple unmatched pitches on both sides, the chart shows separate residual pitches without inventing substitutions. Those residual slots do not individually establish wrong/missed/extra identities; overall counts still come from the alignment rules above.

Bar height represents MIDI pitch. Bar width does not represent duration, and horizontal spacing does not represent elapsed time. Percentages with an undefined denominator are displayed as `N/A` in chart summaries.

## Limitations and troubleshooting

This is a preliminary pitch-order test. A fixed 50 ms window can split uneven chords or combine fast melodic notes, affecting alignment and scores. Tempo, rhythm, note duration, and pedal behavior are not scored. MIDI channels are combined rather than evaluated independently.

If imports fail, install dependencies with the exact interpreter used to run the script. If the input port cannot be opened, check the device connection, list the available names again, and close other applications using the port. The accuracy scripts continue analysis after input errors, so inspect terminal error messages before interpreting an all-missed result as a performance error.

Charts use Matplotlib's noninteractive `Agg` backend and are saved without opening a GUI window. All recorded note events remain in memory; the chart script exports PNGs, not a MIDI recording or a machine-readable alignment report.
