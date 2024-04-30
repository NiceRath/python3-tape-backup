# Python3 - Tape Backup Script

This is a generic Python3 script that is able to back-up directories to tapes/tape-libraries using [tar](https://linux.die.net/man/1/tar) & [mtx](https://linux.die.net/man/1/mtx).

Tested with [Dell PowerVault TL1000](https://www.dell.com/en-us/shop/data-storage-and-backup/powervault-tl1000/spd/storage-tl1000/pw_tl1000_11595).

It is meant to be lightweight.

A status e-mail with some stats is sent once the backup is finished.

You can test it by setting `TRY_RUN` to `True`. This will create snapshots and move cartridges inside the tape library - but skip the actual backup.

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

**WARNING**: This kind of restore can take a long time, as it needs to read the whole tape archive. (*as it has no index*)

```bash
DEV_LIBRARY='/dev/st0'  # number might differ

# optional: read tape content
tar -tvf "$DEV_LIBRARY" --blocking-factor 2048
mt -f "$DEV_LIBRARY" rewind

# restore
DEST_PATH='/tmp/restore'
TO_RESTORE='<DIRECTORY>/<PATH-TO-FILE-OR-DIR>'
SNAPSHOT_NAME='snap_tape'

mkdir "$DEST_PATH"
tar -xvf "$DEV_LIBRARY" "${SNAPSHOT_NAME}/${TO_RESTORE}" -C "$DEST_PATH" --blocking-factor 2048
```
