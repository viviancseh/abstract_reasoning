
# Manual Installation
## uv
Install uv: https://docs.astral.sh/uv/getting-started/installation/

uv installs the required Python version (3.10) and all packages (including PsychoPy 2024.1.4) itself; versions are pinned in `pyproject.toml` and `uv.lock`. No separate Python, conda or pyenv installation is needed.

## Pylink (library that provides support for communication with Eye Tracker (EyeLink))
In order to run the experiment with Psychopy and Eye Tracking, you will need to install the pylink library from SR Research:
https://psychopy.org/api/hardware/pylink.html

Pylink is not on the regular Python package index, so it is installed from SR Research's own server (`--index-url`). That server only works with `pip` (not with `uv pip`), so pip is added to the environment first. The same commands work on Windows and macOS.

Pylink also needs the EyeLink Developers Kit to be installed on the computer (usually already the case on the lab's eye-tracking computer); see https://www.sr-research.com/support/.

1. open the project's directory ("experiment-Lab") in the command line
2. run `uv sync` to create the environment
3. add pip to the environment: `uv pip install pip`
4. install pylink: `uv run python -m pip install --index-url=https://pypi.sr-research.com sr-research-pylink`

Note: `uv sync` removes packages that are not listed in `pyproject.toml`, including pip and pylink. If you run `uv sync` again later, repeat steps 3 and 4.

# Run the experiment
1. open the project's directory ("experiment-Lab") in the command line
2. run the following line: `uv run python experiment.py`
