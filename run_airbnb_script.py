"""
Runs the parallax pipeline once per line of the Airbnb origin-story script,
each clip trimmed to its real narration duration, then concatenates them
into one final video with ffmpeg.
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pipeline  # noqa: E402

SCRIPT = [
    {
        "line": "In 2007, two broke roommates in San Francisco couldn't cover rent.",
        "brief": "two roommates worried about rent money in a small apartment",
        "duration_s": 5.09,
    },
    {
        "line": "A huge design conference was in town, every hotel booked solid.",
        "brief": "a fully booked hotel with a no vacancy sign and a crowd",
        "duration_s": 4.66,
    },
    {
        "line": "So Brian Chesky and Joe Gebbia had an idea: three air mattresses, right on their apartment floor.",
        "brief": "three air mattresses laid out on an apartment floor",
        "duration_s": 6.31,
    },
    {
        "line": "Three guests said yes. Eighty dollars each.",
        "brief": "guests with suitcases and dollar bills",
        "duration_s": 4.44,
    },
    {
        "line": "That tiny experiment became Airbnb — now worth over eighty billion dollars.",
        "brief": "a rising growth chart in front of a city skyline",
        "duration_s": 5.47,
    },
    {
        "line": "Sometimes the biggest company starts with the smallest idea, and a spare room.",
        "brief": "a cozy spare room with a glowing lightbulb idea",
        "duration_s": 4.94,
    },
]

import json
import storyboard_generator

OUT_ROOT = pathlib.Path(__file__).resolve().parent / "output" / "airbnb_script"

def main():
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    
    print("\n===== Generating Storyboard =====")
    storyboard = []
    for i, scene in enumerate(SCRIPT, start=1):
        print(f"Generating storyboard for Scene {i}...")
        scene_data = storyboard_generator.generate_scene_storyboard(scene["brief"], i)
        storyboard.append(scene_data)
        
    storyboard_dir = OUT_ROOT / "storyboard"
    storyboard_dir.mkdir(exist_ok=True)
    
    json_path = storyboard_dir / "storyboard.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2)
        
    mockup_path = storyboard_dir / "mockup.png"
    storyboard_generator.draw_mockup(storyboard, str(mockup_path))
    print(f"Storyboard complete. Mockup saved to {mockup_path}")

    clip_paths = []

    for i, scene in enumerate(SCRIPT, start=1):
        run_id = f"scene{i}"
        print(f"\n===== Scene {i}/6 ({scene['duration_s']}s): {scene['line']!r} =====")
        summary = pipeline.run(
            brief=scene["brief"],
            caption=scene["line"],
            run_id=run_id,
            out_root=str(OUT_ROOT),
            duration_s=scene["duration_s"],
            atmosphere={"fog": {"color": "#1a1f2b", "near": 250, "far": 1300}},
            storyboard_scene=storyboard[i-1],
        )
        if summary.get("video"):
            clip_paths.append(summary["video"])
        else:
            print(f"  WARNING: scene {i} produced no video, skipping in final cut")

    if not clip_paths:
        print("No clips rendered — nothing to concatenate.")
        return

    concat_list = OUT_ROOT / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{pathlib.Path(p).resolve().as_posix()}'\n")

    final_path = OUT_ROOT / "airbnb_full.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(final_path)],
        check=True, capture_output=True,
    )
    print(f"\nFinal concatenated video: {final_path}")


if __name__ == "__main__":
    main()
