"""
Sets up the config variables with the info.json for Heroku
"""
import json
from pathlib import Path
import subprocess

def main():
    # Try to find the info.json right here, and run Heroku to set the vars
    infoPath = Path("info.json")
    if not infoPath.exists():
        raise RuntimeError("info.json must exist with the required keys and values")

    # Load and then dump the JSON to make sure that the json data is flattened
    with infoPath.open("r", encoding="UTF-8") as infoFile:
        infoJSON = json.dumps(
            json.load(infoFile)
        )

    subprocess.run([
        "heroku",
        "config:set",
        # Set the config var with the contents of the file
        f"info.json={infoJSON}",
    ], check=True)

if __name__ == "__main__":
    main()
