#!/usr/bin/env python3

import argparse
import pathlib
from lmd.cli import AbstractCLICommand
from typing import Optional, List
from lmd import logger

Arguments = List[str]
RSYNC_DESTINATION_PATH = "/tmp/".rstrip('/')

class CLIRunCommand(AbstractCLICommand):

    KEY = 'run'

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
            "-m",
            "--mode",
            action="store",
            type=str,
            default=None,
            choices=["ssh", "docker", "slurm", "singularity", "sing-slurm"],
            help="What mode to run",
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
        parser.add_argument(
            "-d",
            "--disown",
            action="store_true",
            help="Do not block to wait for the process to exit. stdout/stderr will not be shown with this option.",
        )
        parser.add_argument(
            "-L",
            "--local-forward",
            type=str,
            action="store",
            help="Local port forwarding. A general format is '1234:hostname:2345'."
                 "You can also just specify a number like '8080'. This is interpreted as '8080:localhost:8080."
                 "This cannot be used with -d/--disown option.",
        )
        parser.add_argument(
            "-R",
            "--remote-forward",
            action="store",
            help="Remote port forwarding. A general format is '1234:hostname:2345'."
                 "You can also just specify a number like '8080'. This is interpreted as '8080:localhost:8080."
                 "This cannot be used with -d/--disown option.",
        )
        parser.add_argument(
            "-X",
            "--x-forward",
            action="store_true",
            help="X11 forwarding",
        )
        parser.add_argument(
            "--entrypoint",
            action="store",
            help="Docker entrypoint",
        )
        parser.add_argument(
            "-n",
            "--num-sequence",
            action="store",
            type=int,
            default=1,
            help="number of sequence in Slurm sequential jobs"
        )
        # TEMP: Sweep functionality
        parser.add_argument(
            "--sweep",
            action="store",
            type=str,
            help="specify sweep range (e.g., --sweep 1-255) this is reflected to envvar $LMD_RUN_SWEEP_IDX"
                 "Temporarily only available for slurm mode."
        )
        # TEMP:
        parser.add_argument(
            "--conf",
            action="store",
            type=str,
            help="slurm / docker / singularity custom config"
        )
        parser.add_argument(
            "remote_command",
            default=False,
            action="store",
            nargs="+",
            type=str,
            help="Command to execute in a remote machine.",
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


        # NOTE: Order to check 'mode'
        # 1. If specified in cli --> use that mode
        # 2. If default_mode is set (config file) --> use that mode
        # 3. Use ssh mode
        mode = parsed.mode if parsed.mode else machine_conf.get('default_mode', 'ssh')

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


        # TODO: Clean it up later!!
        # Set environment variables
        # TEMP: only check envvars from conf['project']['environment']
        if project_conf and 'environment' in project_conf:
            env = project_conf['environment']
        else:
            env = {}

        machine_envs = machine_conf.get('environment')
        if machine_envs:
            env.update(machine_envs)

        if mode == "ssh":
            from lmd.machine import SSHMachine
            ssh_machine = SSHMachine(ssh_client, project)
            ssh_machine.execute(parsed.remote_command, relative_workdir, startup=machine_conf.get('startup'),
                                         disown=parsed.disown, x_forward=parsed.x_forward, env=env, dry_run=parsed.dry_run)


        elif mode == "docker":
            from docker import DockerClient
            from lmd.config import DockerContainerConfig
            from lmd.machine import DockerMachine
            base_url = "ssh://" + remote_conf.base_uri
            docker_client = DockerClient(base_url=base_url, use_ssh_client=True)

            if parsed.dry_run:
                raise ValueError('dry run is not yet supported for Docker mode')


            # TODO: This looks horrible. Clean up.
            # If args.image is given, look for the corresponding name in config
            # if not found, regard it as a full name of the image
            # NOTE: Currently it only parses image name !!!
            if parsed.image:
                if parsed.image in config['docker-images']:
                    image = config['docker-images'].get(parsed.image).get('name')
                else:
                    image = parsed.image
            else:
                image = machine_conf.get('docker').get('name')
            if image is None:
                raise RuntimeError("docker image cannot be parsed. Something may be wrong with your docker configuration?")

            docker_conf = DockerContainerConfig(image, f'{user}-{project.name}')
            logger.info('docker_conf: {docker_conf}')

            docker_machine = DockerMachine(docker_client, project, docker_conf=docker_conf)
            docker_machine.execute(parsed.remote_command, relative_workdir, startup=machine_conf.get('startup'),
                                   shell=True, use_gpus=True, x_forward=parsed.x_forward, env=env)

        elif mode == "slurm":
            from lmd.config import SlurmConfig
            from lmd.machine import SlurmMachine

            # TODO: Also parse from slurm config options (aside from default)
            # Parse slurm configuration
            if parsed.conf:
                sconf = config['slurm-configs'].get(parsed.conf)
                if sconf is None:
                    raise KeyError(f'configuration: {parsed.con} cannot be found in "slurm-configs".')
            else:
                sconf = machine_conf.get('slurm')

            slurm_conf = SlurmConfig(**sconf)
            logger.info(f'slurm_conf: {slurm_conf}')

            slurm_machine = SlurmMachine(ssh_client, project, slurm_conf)

            if parsed.sweep:
                assert parsed.disown, "You must set -d option to use sweep functionality."
                begin, end = [int(val) for val in parsed.sweep.split('-')]
                assert begin < end

                for sweep_idx in range(begin, end):
                    env.update({'LMD_RUN_SWEEP_IDX': sweep_idx})
                    slurm_machine.execute(parsed.remote_command, relative_workdir, startup=machine_conf.get('startup'),
                                          interactive=not parsed.disown, num_sequence=parsed.num_sequence, env=env, dry_run=parsed.dry_run, sweeping=True)
            else:
                slurm_machine.execute(parsed.remote_command, relative_workdir, startup=machine_conf.get('startup'),
                                      interactive=not parsed.disown, num_sequence=parsed.num_sequence, env=env, dry_run=parsed.dry_run)

        elif mode == "singularity":
            raise NotImplementedError()
        elif mode == "sing-slurm":
            raise NotImplementedError()
        else:
            raise KeyError('mode: {parsed.mode} is not available.')

        # Rsync remote outdir with the local outdir.
        if project.out_dir:
            from lmd.cli.utils import rsync
            # Check if there's any output file (the first line is always 'total [num-files]')
            result = ssh_client.run(f'ls -l {project.remote_outdir} | grep -v "^total" | wc -l', hide=True)
            num_output_files = int(result.stdout)
            logger.info(f'{num_output_files} files are in the output directory')
            if num_output_files:
                rsync(source_dir=ssh_client.uri(project.remote_outdir), target_dir=project.out_dir, dry_run=parsed.dry_run)




        # TODO: later; Set up port forwarding
        # Check confilicts in parsed arguments
        # assert not ((parsed.disown and parsed.local_forward) or (parsed.disown and parsed.remote_forward)), '-d/--disown option cannot be used with port forwarding option.'
        # conn = machine.remote_conf.get_connection()

        # if parsed.local_forward:
        #     if ':' in parsed.local_forward:
        #         local_port, remote_host, remote_port = parsed.local_forward.split(':')
        #     else:
        #         local_port, remote_host, remote_port = parsed.local_forward, 'localhost', parsed.local_forward

        #     port_forward = lambda : conn.forward_local(local_port, remote_port, remote_host)
        #     # TODO: I should write a new context manager that internally either use local/remote forward or none.
        #     # You can actually copy & paste from the source. The lines of code is not that big.
