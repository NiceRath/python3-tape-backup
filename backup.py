#!/usr/bin/python3

from pathlib import Path
from time import sleep
from datetime import datetime
from traceback import print_exc
from sys import exit as sys_exit
from subprocess import Popen as subprocess_popen
from subprocess import PIPE as subprocess_pipe

# tested with Dell TL1000 tape library & LVM
# creates LVM shapshot of encrypted LVM volume and uses it as backup-source
# to create an encrypted LVM volume - use: https://gist.github.com/NiceRath/c794caa26a28fc90fc628a047648722b
# as cryptmount script-example - see: https://gist.github.com/NiceRath/65511409c8dbbbbb98ae6f1a668b7d5d

# it is doing the following:
#   1. checking which sg device is active
#   2. splitting the folders to multiple tapes by checking their size
#   3. checking if a tape is currently loaded => if so unload it
#   4. filtering the provided tape slots by their status
#   5. splitting the folder to process to multiple tapes (if necessary)
#   6. unloading previously loaded tape (if there is one)
#   7. backup loop:
#     per tape slot run a folder-loop
#       per folder run a tar process (serially)
#   8. format and log/mail tar stati for admin-info

# EDIT THESE VARIABLES AS NEEDED:
PROCESS_SLOTS = [1, 2, 3, 4, 5, 6, 7, 8]  # you could run two instances of this script and split-up the used slots
SNAP_MOUNT = "/snap_tape"
SNAP_SIZE = "1000G"
SNAP_VG = "<YOUR-VG>"
SNAP_LV = "<YOUR-LVM>"
SNAP_SRC = f"/dev/{SNAP_VG}/{SNAP_LV}"
SNAP_NAME = 'snap_tape'  # must match fstab mount !!
CRYPTMOUNT_SCRIPT = '/usr/local/sbin/cryptmount/cryptmount.sh'
CRYPTMOUNT_PREFIX = 'crypt'
MAIL_FROM = '<YOUR-SENDER>'
MAIL_TO = '<YOUR-ADMIN>'
TAPE_CAPACITY_TB = 10  # you might need to tweak this value - it's about the 'uncompressed' tape size - a little lower
# NOTE: folder are relative from the LVM mountpoint
DIR_TAPE_CONTENT_INDEX = 'tape_content_index'
DIR_EXCLUSIONS = ['lost+found', 'restore', DIR_TAPE_CONTENT_INDEX]
DIR_INCLUSIONS = []  # if defined => only the ones listed will be processed (for manual backup of some folder)
SPECIAL_TAPE_PREFIXES = []  # folders with those prefixes will be placed on their own tape

SEND_MAIL = f"/usr/sbin/sendmail -F '{MAIL_FROM}' -f '{MAIL_FROM}' -t '${MAIL_TO}'"
ST_DEV = 'st0'
ST_BLOCK_SIZE = '64k'
TAPE_LABEL_LENGTH = 8
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
TIME_FORMAT_FILE = '%Y-%m-%d_%H-%M-%S'
TAPE_CAPACITY_MB = TAPE_CAPACITY_TB * 1_000_000
DEBUG = True
TRY_RUN = False  # this will create snapshots and move cartridges inside the tape library - but skip the actual backup.
TAR_CMD = 'tar -chf'
TAR_ARGS = '--blocking-factor 2048'
STATUS_SUCCESS = 'SUCCESS'


class TapeBackup:
    def __init__(self):
        self.SG_DEV = None
        self.STATUS = STATUS_SUCCESS
        self.MODE = ''
        self.ERROR_MSGS = []
        self.BACKUP_SRC_PATH = self._shell(f'df -h | grep {SNAP_VG}-{SNAP_LV}')[0].rsplit(' ', 1)[1]
        self.UNPROCESSED = []

    def start(self):
        try:
            self._log('Starting tape backup process')

            if TRY_RUN:
                self.MODE = 'TRY-MODE - '
                self._log('INFO: RUNNING SCRIPT IN TRY-MODE')

            start_time = datetime.now()

            # prerequisites
            self.SG_DEV = self._get_active_sg()
            dirs = self._get_backup_dir_sizes()
            self._unload_transfer()
            available_slots = self._get_available_slots()
            slot_dir_mapping = self._get_dir_slot_mapping(dirs=dirs, slots=available_slots)

            # starting actual backup process
            self._create_snapshot()
            stats = self._backup(
                slot_dir_mapping=slot_dir_mapping,
                dirs=dirs,
                slots=available_slots,
            )
            stats_str = self._format_stati(
                stats=stats,
                start_time=start_time,
                error_msgs=self.ERROR_MSGS
            )
            self._remove_snapshot()

            # post-processing
            self._log('Finished tape backup process')
            self._log(f'Backup job stati:\n{stats_str}')
            self._mail(
                subj=f"{self.MODE}{self.STATUS}",
                body=f"Backup job stati:\n\n{stats_str}"
            )
            if self.STATUS != STATUS_SUCCESS:
                sys_exit(1)

        except (Exception, KeyboardInterrupt) as error:
            # clean-up snapshot etc. if backup fails hard
            print_exc()
            self._unload_transfer()
            self._remove_snapshot()
            self._error(msg=error)

    # methods for actively backing-up

    def _backup(self, slot_dir_mapping: dict, dirs: dict, slots: dict) -> dict:
        #   {SLOT_ID: {'label': SLOT_LABEL, 'result': {'start_time': START_TIME, 'stop_time': STOP_TIME, 'exit_code', EXIT_CODE, dirs: {'size': SIZE, 'size_mb': SIZE_MB} } } }
        self._log('Starting backup loop')
        result_dict = {}

        for slot_id, dir_list in slot_dir_mapping.items():
            if len(dir_list) == 0:
                continue

            self._load_tape(slot_id=slot_id)
            slot_label = slots[slot_id][0]
            tape_result_dict = {}
            self._log(f"Backing-up to tape in slot '{slot_id}' with label '{slot_label}'")

            tar_stats = self._tar(dir_list=dir_list, dir_sizes=dirs)

            result_dict[slot_id] = {'label': slot_label, 'result': tar_stats}
            self._unload_transfer()

        self._log('Finished backup loop')
        return result_dict

    def _tar(self, dir_list: list, dir_sizes: dict) -> dict:
        # NOTE: we first tried a per-directory loop with 'tar append (arf)'
        #       but this took forever to analyze before the copying process started
        #       per example: for a 17 MB backup directory it took from 16:54:49 to 18:54:28 -.-

        backup_src_list = [self._create_content_index_file(dir_list)]
        stats = {'start_time': datetime.now()}
        dir_stats = {}

        for d in dir_list:
            size_mb = int(dir_sizes[d])
            if size_mb > 1000000:
                dir_size = "%.2f TB" % (size_mb / 1000000)

            elif size_mb > 1000:
                dir_size = "%.2f GB" % (size_mb / 1000)

            else:
                dir_size = f"{size_mb} MB"

            dir_stats[d] = {'size': dir_size, 'size_mb': size_mb}
            backup_src_list.append(f'{SNAP_MOUNT}/{d}')

        if TRY_RUN:
            exit_code = 0
            sleep(10)

        else:
            _, exit_code = self._shell(
                f"{TAR_CMD} /dev/{ST_DEV} {' '.join(backup_src_list)} {TAR_ARGS}",
                exit_code=True,
            )

        if exit_code != 0:
            self.STATUS = 'FAILED'

        stats['exit_code'] = exit_code
        stats['stop_time'] = datetime.now()
        stats['dirs'] = dir_stats
        return stats

    def _create_snapshot(self):
        self._log('Creating backup snapshot')
        self._shell(f'lvcreate -L{SNAP_SIZE} -s -n {SNAP_NAME} {SNAP_SRC}')
        self._shell(f"bash {CRYPTMOUNT_SCRIPT} {SNAP_VG}-{SNAP_NAME} {CRYPTMOUNT_PREFIX}-{SNAP_NAME} {SNAP_MOUNT} ''")

    def _remove_snapshot(self):
        self._log('Removing backup snapshot')
        self._shell(f'umount {SNAP_MOUNT}')
        self._shell(f'cryptsetup luksClose /dev/mapper/{CRYPTMOUNT_PREFIX}-{SNAP_NAME}')
        self._shell(f'lvremove /dev/{SNAP_VG}/{SNAP_NAME} -y')

    def _create_content_index_file(self, dir_list: list) -> str:
        index_tmp_file = '/tmp/tape_content_index.txt'
        index_file = Path(
            f'{self.BACKUP_SRC_PATH}/{DIR_TAPE_CONTENT_INDEX}/'
            f'tape_content_index_{datetime.now().strftime(TIME_FORMAT_FILE)}.txt'
        )

        for d in dir_list:
            self._shell(f'cd / && find {SNAP_MOUNT[1:]}/{d} -type f >> {index_file}')

        self._shell(f'cp {index_file} {index_tmp_file}')
        return index_tmp_file

    # methods for tape interactions

    def _load_tape(self, slot_id: int):
        self._log(f"Loading tape from slot '{slot_id}'")
        self._shell(f'mtx -f /dev/{self.SG_DEV} load {slot_id}')
        self._shell(f'mt -f /dev/{ST_DEV} rewind')  # just to be sure
        self._shell(f'mt -f /dev/{ST_DEV} setblk {ST_BLOCK_SIZE}')  # faster read/write

    def _get_available_slots(self) -> dict:
        # { SLOT_ID: SLOT_LABEL }
        available =  {}

        for slot in PROCESS_SLOTS:
            if self._slot_full(slot):
                label = self._shell(f"mtx -f /dev/{self.SG_DEV} status 2> /dev/null | grep '{slot}:' | tail -c {TAPE_LABEL_LENGTH}")
                available[slot] = label

        if DEBUG:
            self._log(f"TAPE SLOTS: '{available}'")

        return available

    def _get_active_sg(self) -> str:
        sg_list = self._shell('ls /dev/ | grep sg[0-9]')

        for sg_dev in sg_list:
            sg_status = self._shell(f'mtx -f /dev/{sg_dev} status 2> /dev/null')

            if DEBUG:
                self._log(f"Status of sg device '{sg_dev}': '{sg_status}'")

            if len(sg_status) > 0:
                self._shell(f'mtx -f /dev/{sg_dev} inventory')
                return sg_dev

    def _slot_full(self, slot_id) -> bool:
        slot_status = self._shell(f"mtx -f /dev/{self.SG_DEV} status 2> /dev/null | grep '{slot_id}:' | cut -d ':' -f2 | cut -d ' ' -f1")

        if ''.join(slot_status).find('Full') != -1:
            return True

        else:
            if DEBUG:
                self._log(f"Tape slot '{slot_id}' is empty")

            return False

    def _unload_transfer(self):
        status = self._shell(f"mtx -f /dev/{self.SG_DEV} status 2> /dev/null | grep 'Data Transfer Element' | cut -d ':' -f2 | cut -d ' ' -f1")

        if DEBUG:
            self._log(f"Transfer status: '{status}'")

        if len(status) > 0 and status[0] != 'Empty':
            label = self._shell(f"mtx -f /dev/{self.SG_DEV} status 2> /dev/null | grep 'Data Transfer Element' | tail -c {TAPE_LABEL_LENGTH}")[0]
            self._log(f"Unloading previously loaded tape with label '{label}'")
            self._shell(f'mt -f /dev/{ST_DEV} rewind')
            self._shell(f'mtx -f /dev/{self.SG_DEV} unload')
            self._shell(f'mtx -f /dev/{self.SG_DEV} inventory')

        else:
            self._log('Transfer is empty and ready for use!')

    # methods for internal purposes

    def _get_backup_dir_sizes(self) -> dict:
        dir_list = self._shell(f'du -smL {self.BACKUP_SRC_PATH}/* ')
        dir_dict = {}

        for dir_str in dir_list:
            size_mb = dir_str.split('\t', 1)[0]
            name = dir_str.rsplit('/', 1)[1]

            if len(DIR_INCLUSIONS) > 0:
                # if inclusions are set => filter available dirs to the ones we want to back-up
                if name in DIR_INCLUSIONS:
                    dir_dict[name] = size_mb

            else:
                dir_dict[name] = size_mb

        if DEBUG:
            self._log(f"Directories: '{dir_dict}'")

        return dir_dict

    def _get_dir_slot_mapping(self, dirs: dict, slots: dict) -> dict:
        # { SLOT_ID: [ DIR1, DIR2] }
        slot_dir_mapping = {}
        placed_dir_list = []  # to keep track of process dirs

        for slot in slots.keys():
            dir_size = 0
            dir_list = []
            dir_skip = []
            tape_prefix = None

            # check if prefix-filter should be applied (any prefixed dirs are unprocessed)
            for name in dirs.keys():
                if tape_prefix is not None:
                    break

                if name in DIR_EXCLUSIONS or name in placed_dir_list:
                    continue

                for prefix in SPECIAL_TAPE_PREFIXES:
                    if name.startswith(f'{prefix}_'):
                        tape_prefix = prefix
                        break

            if DEBUG:
                if tape_prefix is not None:
                    self._log(f"FILTERING FOR PREFIX {tape_prefix}")

                self._log(f"SLOT {slot} | ALREADY PLACED {placed_dir_list}")

            # filter dirs by prefix and/or size
            for name, size in dirs.items():
                if name in DIR_EXCLUSIONS or name in placed_dir_list:
                    continue

                if tape_prefix is not None and not name.startswith(f'{tape_prefix}_'):
                    if DEBUG:
                        self._log(f"SLOT {slot} | DIRECTORY {name} | NOT PREFIXED")

                    continue

                _size = (dir_size + int(size))

                if _size < TAPE_CAPACITY_MB:
                    dir_list.append(name)
                    placed_dir_list.append(name)
                    dir_size += int(size)

                else:
                    dir_skip.append(name)
                    if DEBUG:
                        self._log(f"SLOT {slot} | DIRECTORY {name} | TOO BIG ({dir_size} + {size} > {TAPE_CAPACITY_MB})")

            self._log(f"SLOT {slot} | PREFIX {tape_prefix} | PLACED {placed_dir_list} | THIS {dir_list} | SKIP {dir_skip}")
            slot_dir_mapping[slot] = dir_list

        processed_dirs = placed_dir_list + DIR_EXCLUSIONS
        unprocessed_dirs = [d for d in dirs if d not in processed_dirs]
        if len(unprocessed_dirs) > 0:
            self.STATUS = 'ERROR'
            self.UNPROCESSED = unprocessed_dirs
            self._log(
                f"ERROR: Not all dirs fit on the available tape slots! "
                f"Unprocessed dirs: '{unprocessed_dirs}'"
            )

        self._log(f'Tape to directory mapping: {slot_dir_mapping}')
        return slot_dir_mapping

    def _shell(self, cmd: str, exit_code=False) -> (list, tuple):
        output = self.__process(cmd)

        if DEBUG:
            self._log(f"Shell command: '{cmd}'")
            self._log(f"Shell output: '{output}'")

        output_lines = str(output[0]).split('\n')
        output_exit_code = int(output[2])
        output_error = output[1]

        if output_error not in [None, ''] and output_error.find('Removing leading') == -1:
            self._log(f"Got error while executing command: '{output_error}'")
            if exit_code and output_exit_code != 0:
                self.ERROR_MSGS.append(output_error)

        parsed_lines = []

        for line in output_lines:
            line = line.strip()
            if line not in ['.', '..', '']:
                parsed_lines.append(line)

        if exit_code:
            return parsed_lines, output_exit_code

        return parsed_lines

    def _format_stati(self, stats: dict, start_time: datetime, error_msgs: list) -> str:
        stati_list = [
            f'Backup start time: {start_time.strftime(TIME_FORMAT)}',
            f'Backup finish time: {datetime.now().strftime(TIME_FORMAT)}\n',
            'Change the listed tapes and mark them with the folder-lists!!\n',
        ]

        if len(error_msgs) > 0:
            stati_list.append('Got the following error messages wile processing:')
            for error in error_msgs:
                stati_list.append(error)

        for slot_id, slot_status in stats.items():
            # NOTE: for dict format look into the _backup method
            stati_list.append(f"\nBackup status for tape in slot '{slot_id}' with label '{slot_status['label']}':\n")
            stati_list.append(f"Start time: {slot_status['result']['start_time'].strftime(TIME_FORMAT)}")
            stati_list.append(f"Stop time: {slot_status['result']['stop_time'].strftime(TIME_FORMAT)}")
            stati_list.append(f"Exit code: {slot_status['result']['exit_code']}")
            start_ts = datetime.timestamp(slot_status['result']['start_time'])
            stop_ts = datetime.timestamp(slot_status['result']['stop_time'])
            duration_sec = int(stop_ts - start_ts)
            archive_size = 0

            for folder_name, folder_stats in slot_status['result']['folders'].items():
                stati_list.append(f"Folder '{folder_name}' size: {folder_stats['size']}")
                archive_size += folder_stats['size_mb']

            try:
                throughput = archive_size / duration_sec

            except ZeroDivisionError:
                throughput = float(0)

            try:
                archive_size_tb = (archive_size / 1024) / 1024

            except ZeroDivisionError:
                archive_size_tb = 'UNKNOWN'

            stati_list.append('')
            stati_list.append("Full size: %.3f TB" % archive_size_tb)
            stati_list.append("Calculated throughput: %.2f MB/s" % throughput)
            stati_list.append('')

        if len(self.UNPROCESSED) > 0:
            stati_list.append('')
            stati_list.append(f"ERROR: Unprocessed folders: '{self.UNPROCESSED}'")
            stati_list.append('')

        return '\n'.join(stati_list)

    @staticmethod
    def __process(cmd: str) -> tuple:
        sp = subprocess_popen(
            [cmd],
            shell=True,
            stdout=subprocess_pipe,
            stderr=subprocess_pipe
        )
        output, error = sp.communicate()
        rc = sp.returncode

        return output.decode('utf-8'), error.decode('utf-8'), rc

    def _error(self, msg: str):
        _msg = f"An error occurred:\n{msg}"
        self._log(_msg)
        self._mail(subj='ERROR', body=_msg)
        sys_exit(1)

    @staticmethod
    def _log(output: str) -> None:
        print(output)

    def _mail(self, subj: str, body: str) -> None:
        body_newline = body.replace('\n', '\\n')
        result = self._shell(f"echo \"Subject:Tape Backup {subj}\\n\\n{body_newline}\" | {SEND_MAIL}")

        if DEBUG:
            self._log(str(result))


TapeBackup().start()
