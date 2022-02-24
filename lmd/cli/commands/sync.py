#!/usr/bin/env python3

import argparse
import pathlib
from lmd.cli import AbstractCLICommand
from typing import Optional, List
from lmd import logger

Arguments = List[str]
RSYNC_DESTINATION_PATH = "/tmp/".rstrip('/')

class CLISyncCommand(AbstractCLICommand):

    KEY = 'sync'

    @staticmethod
    def parser(parent: Optional[argparse.ArgumentParser] = None,
               args: Optional[Arguments] = None) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(parents=[parent])

        parser.add_argument(
            "machine",
            action="store",
            type=str,
            help="Machine",
        )
        parser.add_argument(
            "-v",
            "--mount",
            default=False,
            const=True,
            action="store",
            nargs="?",
            type=str,
            help="Whether to mount the current project into the container. "
                 "Pass a comma-separated list of paths to mount multiple projects",
        )
        return parser

    @staticmethod
    def execute(config: dict, parsed: argparse.Namespace, relative_workdir: pathlib.Path=pathlib.Path('.')) -> None:
        """Deploy the local repository and execute the command on a machine.

        1. get SSH connection to machine
        2. run rsync(project.root_dir, project.remote_rootdir)
        3. run machine.execute(parsed.remote_command)
        """
        from lmd.cli.utils import rsync

        # Read from lmd config file and reflect it
        # TODO: clean this up
        from lmd.machine import RemoteConfig
        if parsed.machine not in config['machines']:
            raise KeyError(
                f'Machine "{parsed.machine}" not found in the configuration. '
                f'Available machines are: {list(config["machines"].keys())}'
            )
        machine_conf = config['machines'].get(parsed.machine)
        user, host = machine_conf['user'], machine_conf['host']
        remote_conf = RemoteConfig(user, host)

        project_conf = config.get('project')


        from os.path import join as pjoin
        from lmd.project import Project
        project = Project(name=parsed.name,
                          root_dir=parsed.workdir,
                          remote_dir=pjoin(machine_conf['root_dir'], parsed.name) if 'root_dir' in machine_conf else None,
                          out_dir=parsed.outdir)
        logger.info('project: {project}')


        # NOTE: Diff from run.py: 1. no need to know "mode"
        # NOTE: Order to check 'mode'
        # 1. If specified in cli --> use that mode
        # 2. If default_mode is set (config file) --> use that mode
        # 3. Use ssh mode
        # mode = parsed.mode if parsed.mode else machine_conf.get('default_mode', 'ssh')

        from lmd.machine import SSHClient
        ssh_client = SSHClient(remote_conf)

        # TODO: Hmmm ugly... let's fix it later
        # rsync the remote directory
        # A trick to create non-existing directory before running rsync (https://www.schwertly.com/2013/07/forcing-rsync-to-create-a-remote-path-using-rsync-path/)
        if 'rsync' in config:
            exclude = config['rsync'].get('exclude')
        else:
            exclude = []

        if project_conf and 'rsync' in project_conf:
            exclude.extend(project_conf['rsync'].get('exclude', []))

        rsync_options = f"--rsync-path='mkdir -p {project.remote_rootdir} && mkdir -p {project.remote_outdir} && rsync'"
        rsync(source_dir=project.root_dir, target_dir=ssh_client.uri(project.remote_rootdir), options=rsync_options,
              exclude=exclude, dry_run=parsed.dry_run)