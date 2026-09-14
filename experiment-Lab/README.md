
# Manual Installation
## Python version management
conda / pyenv (mac, linux) / pyenv-win (windows)

## uv
Install uv: https://docs.astral.sh/uv/getting-started/installation/

## Python version
python = ">=3.10, <3.11"

## Psychopy (library that provides GUI for experiments)
psychopy = "2024.1.4"

## Pylink (library that provides support for communication with Eye Tracker (EyeLink))
In order to run the experiment with Psychopy and Eye Tracking, you will need to install the pylink library from SR Research:
https://psychopy.org/api/hardware/pylink.html

1. open the project's directory in the command line
2. run `uv sync` to create the environment
3. install pylink directly into the project's venv with pip:
   `.venv/bin/pip install --index-url https://pypi.sr-research.com --no-cache-dir sr-research-pylink`

# Run the experiment
1. open the project's directory ("experiment-Lab") in the command line
2. run the following line: `uv run python experiment.py`
