#!/usr/bin/env python3
import os
from pathlib import Path
from typing import Iterator

def is_system_root(directory: Path):
    return directory == directory.parent

def yield_parents(directory: Path, max_depth=None) -> Iterator[Path]:
    """generator returning the the current directory and ancestors one after another"""
    max_depth = 999 if max_depth is None else max_depth
    for _ in range(max_depth):
        if is_system_root(directory):
            break
        directory = directory.parent
        yield directory


def merge_nested_dict(a, b, path=None, conflict='use_b'):
    """Merge dictionary b into a (can be nested)

    Correspondence: https://stackoverflow.com/a/7205107/7057866
    """
    if path is None: path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_nested_dict(a[key], b[key], path + [str(key)])
            elif a[key] == b[key]:
                pass # same leaf value
            else:
                if conflict == 'use_b':
                    a[key] = b[key]
                elif conflict == 'use_a':
                    pass
                else:
                    raise ValueError('Conflict at %s' % '.'.join(path + [str(key)]))
        else:
            a[key] = b[key]
    return a


def parse_config(project_root):
    """ Parse lmd config (json file)

    It looks for config file in this order:
    1. incrementally goes up in the directory tree (up to LMD_MAX_DEPTH) and find .lmd
    2. $HOME/.lmd.config
    3. $HOME/.config/lmd
    """
    import json
    from os.path import expandvars, isfile

    def _maybe_load(path):
        if isfile(path):
            with open(path, 'r') as f:
                return json.load(f)
        return {}

    # TODO: Hmmm... a better way to write this config search algorithm?
    global_conf_paths = ['${HOME}/.lmd.config', '${HOME}/.config/lmd']
    for path in global_conf_paths:
        path = expandvars(path)
        if isfile(path):
            break
    with open(path, 'r') as f:
        global_conf = json.load(f)

    local_conf = _maybe_load(f'{project_root}/.lmd.config')

    global_conf = remove_recursively(global_conf, key='__help')
    local_conf = remove_recursively(local_conf, key='__help')

    merged_conf = merge_nested_dict(global_conf, local_conf)
    return merged_conf

def remove_recursively(config_dict, key='__help'):
    """remove entry with the specified key recursively."""
    for k in list(config_dict.keys()):
        if k == key:
            del config_dict[k]
            continue
        if isinstance(config_dict[k], dict):
            config_dict[k] = remove_recursively(config_dict[k], key=key)

    return config_dict


def find_project_root():
    """Find a project root (which is rsync-ed with the remote server).

    It first goes up in the directory tree to find ".git" or ".lmd" file.
    If not found, print warning and just use the current directory
    """
    from lmd import logger
    def is_proj_root(directory: Path):
        if (directory / '.git').is_dir():
            return True
        if (directory / '.lmd').is_file():
            return True
        return False

    current_dir = Path(os.getcwd()).resolve()
    if is_proj_root(current_dir):
        return current_dir

    for directory in yield_parents(current_dir):
        if is_proj_root(directory):
            return directory

    logger.warn('.git directory or .lmd file not found in the ancestor directories.\n'
                'Setting project root to current directory')

    assert not is_system_root(current_dir), "project root detected is the system root '/' you never want to rsync your entire disk."
    return current_dir



from datetime import datetime
def get_timestamp() -> str:
    return datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S.%f')

def read_timestamp(time_str: str) -> datetime:
    return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')


def wrap_shebang(command, shell='bash'):
    return f"#!/usr/bin/env {shell}\n{command}"

sacct_cmd = "sacct --starttime $(date -d '40 hours ago' +%D-%R) --endtime now --format JobID,JobName%-100,NodeList,Elapsed,State,ExitCode,MaxRSS --parsable2"


def parse_sacct(sacct_output):
    lines = sacct_output.strip().split('\n')
    keys = lines[0].split('|')
    entries = [{key: entry for key, entry in zip(keys, line.split('|'))} for line in lines[1:]]
    return entries

def posixpath2str(obj):
    import pathlib
    if isinstance(obj, list):
        return [posixpath2str(e) for e in obj]
    elif isinstance(obj, dict):
        return {key: posixpath2str(val) for key, val in obj.items()}
    elif isinstance(obj, pathlib.Path):
        return str(obj)
    else:
        return obj
