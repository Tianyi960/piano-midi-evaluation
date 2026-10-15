import mido
import time


def note_name(note):
    names = ['C', 'C#', 'D', 'D#', 'E', 'F',
             'F#', 'G', 'G#', 'A', 'A#', 'B']
    return f"{names[note % 12]}{note // 12 - 1}"


port = mido.open_input('Roland Digital Piano 0')

start_time = time.perf_counter()
active_notes = {}

print("Recording started...")
print("Press Ctrl+C to stop.")

try:
    while True:
        for msg in port.iter_pending():

            timestamp = time.perf_counter() - start_time

            if msg.type == "note_on" and msg.velocity > 0:
                active_notes[msg.note] = {
                    "start_time": timestamp,
                    "velocity": msg.velocity
                }

                print(
                    f"ON  | timestamp={timestamp:.3f}s | "
                    f"note={note_name(msg.note)} | "
                    f"velocity={msg.velocity}"
                )

            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                if msg.note in active_notes:
                    info = active_notes.pop(msg.note)
                    duration = timestamp - info["start_time"]

                    print(
                        f"OFF | timestamp={timestamp:.3f}s | "
                        f"note={note_name(msg.note)} | "
                        f"velocity={info['velocity']} | "
                        f"duration={duration:.3f}s"
                    )

        time.sleep(0.001)

except KeyboardInterrupt:
    print("\nStopped.")

finally:
    port.close()
