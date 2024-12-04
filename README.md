# Python3 - Tape Backup Script

This is a generic Python3 script that is able to back-up directories to tapes/tape-libraries using [tar](https://linux.die.net/man/1/tar) & [mtx](https://linux.die.net/man/1/mtx).

Tested with [Dell PowerVault TL1000](https://www.dell.com/en-us/shop/data-storage-and-backup/powervault-tl1000/spd/storage-tl1000/pw_tl1000_11595).

It is meant to be lightweight.

A status e-mail with some stats is sent once the backup is finished.

You can test it by setting `TRY_RUN` to `True`. This will create snapshots and move cartridges inside the tape library - but skip the actual backup.

The first file on the tape is a **tape-content-index** that can be used when restoring data!

If there are many small files - the throughput will be low. With larger files we've seen 150-300MB/s with TLO7 (uncompressed).

```text
Backup job stati:

Backup start time: 2024-02-06 18:00:00
Backup finish time: 2024-02-08 03:06:53

Change the listed tapes and mark them with the folder-lists!!

...

Backup status for tape in slot '5' with label 'D0018M8':

Start time: 2024-02-08 01:03:12
Stop time: 2024-02-08 03:05:40
Exit code: 0
Folder 'archive' size: 10 MB
Folder 'app1' size: 271 MB
Folder 'app2' size: 307 MB
Folder 'app3' size: 358.06 GB
Folder 'app4' size: 84.67 GB
...

Full size: 0.428 TB
Calculated throughput: 61.02 MB/s

...
```

----

## Restore

Warning: Tape restores can take a long time.

Make sure no Tape-Backup-Job is started while restoring data!

### Prerequisites

Load tape to restore from.

### Get active SG-device

```bash
ls /dev/ | grep sg[0-9]
mtx -f /dev/sgN status 2> /dev/null  # correct one displays the current inventory
```

Update the `/dev/sg0` references in the other commands.

### Check what tape to load

```bash
mtx -f /dev/sg0 inventory
mtx -f /dev/sg0 status
```

### Load tape

```bash
mtx -f /dev/sg0 load <ID>
```

### Find data to restore

#### Check the local tape-content index-file

```bash
ls -l ${BACKUP_PATH}/tape_content_index/
less ${BACKUP_PATH}/tape_content_index/tape_content_index_${DATETIME}.txt
```

#### OR Check the tape-content index-file

```bash
RESTORE_PATH="${BACKUP_PATH}/restore"
mkdir "$RESTORE_PATH"
tar -xvf /dev/st0 -C "$RESTORE_PATH" --blocking-factor 2048 "tmp/tape_content_index.txt"
# press CTRL+C after a few seconds
less "${BACKUP_PATH}/restore/tmp/tape_content_index.txt"
```

#### OR read the full tape content (VERY SLOW)

```bash
tar -tvf /dev/sg0 --blocking-factor 2048 | tee -a tape_content.log
mt -f /dev/sg0 rewind
```

### Restore

```bash
RESTORE_PATH="${BACKUP_PATH}/restore"
TO_RESTORE='<PATH-TO-FILE-OR-DIR>'  # find in tape-content-index

mkdir "$RESTORE_PATH"
tar -xvf /dev/sg0 -C "$RESTORE_PATH" --blocking-factor 2048 "$TO_RESTORE" >> /var/log/backup/tape_restore.log 2>&1 &

# put the restore process into the background (session independent)
jobs
disown

# check if process is still running
ps -aux | grep tar
```

### Unload tape

```bash
mt -f /dev/st0 rewind
mtx -f /dev/sg0 unload
```
