"""Package defaults must not write into installation directories. Author: chatgpt_gpt6."""
import json
import os
from pathlib import Path
import subprocess
import sys


def read_config(environment):
    result=subprocess.run([sys.executable,'-c','import json; from govparse.config import DATA,SOURCE_DIR; print(json.dumps([str(DATA),str(SOURCE_DIR)]))'],env=environment,text=True,capture_output=True,check=True)
    return [Path(x) for x in json.loads(result.stdout)]


def test_default_data_and_input_are_user_owned():
    env=os.environ.copy()
    env.pop('GOVPARSE_DATA',None);env.pop('GOVPARSE_SOURCE',None)
    data,source=read_config(env)
    assert data==Path.home()/'.govparse'
    assert source==data/'input'


def test_explicit_paths_override_defaults(tmp_path):
    env={**os.environ,'GOVPARSE_DATA':str(tmp_path/'outputs'),'GOVPARSE_SOURCE':str(tmp_path/'input-docs')}
    assert read_config(env)==[tmp_path/'outputs',tmp_path/'input-docs']
