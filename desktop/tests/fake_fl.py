"""Stands in for FL Studio in tests: '/R /Ewav project.flp' writes project.wav next to the project."""
import pathlib
import subprocess
import sys
import time

import imageio_ffmpeg

flp = pathlib.Path(sys.argv[-1])
time.sleep(1)
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=20", "-ac", "2", "-ar", "44100",
                str(flp.with_suffix(".wav"))], check=True)
