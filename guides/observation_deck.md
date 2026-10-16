# Observation Deck

## Introduction

**Observation Deck** is a library view of your imaging archive. You point it at one or more master folders, and it reads the headers of every image inside them to show how much time you spent on each target, on which nights, and through which filters.

It only reads your images. The library list is saved in CAst's own database, so reopening the mode does not rescan your folders.

### What you can do with this mode

- **See your archive at a glance.** The header shows total **Integration**, **Targets**, **Nights**, and **Frames**.
- **Browse the library.** The **Library** tree lists each master folder, its subfolders, and every image with its type and filter.
- **Review your observing history.** The history view shows nights, months, and years of imaging. Hover a night to see its targets, integration, frame count, and filters.
- **Compare filters and targets.** The filter usage card and the **Targets** table break the time down by filter and by target.
- **Inspect headers.** Select one image to read its FITS or XISF keywords in **Metadata**.
- **Leave images out of the statistics.** Exclude duplicate copies or frames you do not want counted. The files are not changed.

---

## How It Works

### Step 1: Open Observation Deck

Observation Deck is not a launcher card. Choose **Mode > Observation Deck**.

The first time, the deck is empty. After that, it restores the saved library in the background without scanning folders.

### Step 2: Add a Master Folder

Click **+** next to **Library**, or open the **•••** menu and choose **Add Master Folder…**. Pick the folder that holds your imaging sessions, for example `D:\Astrophotography`.

CAst scans every supported image under that folder and shows `Scanning n/total` while it reads headers. Each top-level subfolder becomes a target, so `D:\Astrophotography\M13\Lights\...` counts toward **M13**.

You can add more than one master folder. A folder that sits inside a master already in the library is covered by that master.

### Step 3: Read the Statistics

Select **Library** to see every target, a master folder to see one archive, or a folder or image to narrow the statistics to that part of the tree.

CAst sorts each image into a type: light subframe, calibrated, registered, or normalized light, integration, master calibration, calibration frame, or other image. To avoid counting the same exposure twice, it counts only the earliest processing stage it finds for each target. For example, if a target has raw lights and calibrated lights, only the raw lights count toward integration.

Use **Search targets and images…** and the type list above the tree to find files quickly.

### Step 4: Leave Images Out of the Statistics

Right-click images or folders in the **Library** tree and choose **Exclude from Statistics**. Excluded images stay in the tree, shown dimmed in italics, and the summary under the tree shows how many are excluded.

To count them again, select them and choose **Include in Statistics**.

Excluding an image changes only the Observation Deck. The file is not moved, renamed, or deleted. Your choices are kept when you rescan or reopen CAst.

### Step 5: Keep the Library Up to Date

Click **Rescan** after adding or removing images on disk. To drop a master folder from the library, select it and choose **Remove Folder** from the **•••** menu. This only removes it from the deck, not from your disk.

### Step 6: Save the Observation History

Open the **•••** menu in the header and choose **Save Observation History…** to save the history view as a PNG image.

---

## Finding Duplicate Images with the AI Assistant

Archives often contain copies: a backup folder inside the master folder, or the same sub saved twice in different formats. Copies inflate the integration time and frame counts.

Open **Tools > AI Assistant** and ask, for example:

`Build an Observation Deck from D:\Astrophotography, but leave out images that are copies of each other.`

The assistant can add the folder to the deck, look for duplicates, and exclude the copies. It asks for your approval before adding the folder and before excluding anything.

It reports two kinds of duplicates:

- **Exact copies** have identical file contents.
- **Same-exposure copies** record the same exposure (same `DATE-OBS`, exposure time, filter, camera, and image size) but differ in bytes, for example a FITS sub also saved as XISF. These are likely copies, so check the list before excluding them.

A raw light and its calibrated version are not reported as copies, because the deck already counts only one processing stage.

For each group, the assistant suggests keeping the file nearest the top of the folder tree and excluding the rest. Duplicate search only reads your files, and exclusion only changes the deck's statistics.
