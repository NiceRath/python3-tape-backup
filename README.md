# Python3 - Tape Backup Script

This is a generic Python3 script that is able to back-up directories to tapes/tape-libraries using [tar](https://linux.die.net/man/1/tar) & [mtx](https://linux.die.net/man/1/mtx).

Tested with [Dell PowerVault TL1000](https://www.dell.com/en-us/shop/data-storage-and-backup/powervault-tl1000/spd/storage-tl1000/pw_tl1000_11595).

It is meant to be lightweight.

A status e-mail with some stats is sent once the backup is finished.

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