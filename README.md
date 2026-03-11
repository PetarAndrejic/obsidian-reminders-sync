# 📅 obsidian-reminders-sync - Sync Obsidian Notes with Reminders

[![Download Release](https://img.shields.io/badge/Download-obsidian--reminders--sync-blue?style=for-the-badge)](https://github.com/PetarAndrejic/obsidian-reminders-sync/releases)

## 📌 What is obsidian-reminders-sync?

obsidian-reminders-sync lets you keep your Obsidian daily notes and macOS Reminders connected. It updates your notes when you change reminders and adds new reminders from your notes. This works both ways. The app uses Claude Code hooks and runs on macOS. It helps organize tasks without switching apps.

### Who should use this tool?

- People who use Obsidian for note-taking and want to link it to their task reminders.
- Users who want daily notes and reminders to update each other automatically.
- Anyone looking for a basic way to connect productivity apps on macOS.

## 🖥 System Requirements

- macOS 10.15 (Catalina) or later.
- Obsidian installed (the note-taking app).
- Python 3.7 or higher installed on your Mac.
- Access to macOS Reminders app.
- Basic permissions for running AppleScript and Python scripts.

## ⚙️ Key Features

- Two-way sync between Obsidian daily notes and Reminders.
- Updates notes when reminders change.
- Adds new reminders based on notes.
- Uses AppleScript and Claude Code hooks for secure interaction.
- Works quietly in the background.
- Saves time by linking notes and to-dos.

## 🛠 How It Works

The app reads your daily note in Obsidian. It looks for special tags or to-do items. Then it syncs those with the Reminders app on your Mac. If you change a reminder, the note updates on the next sync. If you add or mark a to-do in notes, the reminder updates too. The code hooks ensure this runs smoothly without errors.

---

## 🚀 Getting Started

Before you install, make sure your Mac meets the system requirements above. You will need Python set up, but this guide will not require deep technical skills.

## 🔽 Downloading the App

Click the big badge at the top of this page or visit the link below to get the latest release:

**https://github.com/PetarAndrejic/obsidian-reminders-sync/releases**

This page lists all releases. Download the latest `.zip` or `.dmg` file if available. If the release provides a Python script or package, download the provided files.

## 💻 Installing on macOS

Follow these steps to install and run the software:

1. **Download the release.** Use the link above to go to the release page. Look for the latest version with a filename ending in `.zip` or `.dmg`.
2. **Unpack the files.** If you downloaded a zip file, double-click it to unzip. If it is a dmg, open it to access the installer.
3. **Locate the main script or app.** If it is a Python script, you should see a file like `sync.py` or a similar name.
4. **Run the program:**
   - For Python scripts:
     - Open the Terminal app from your Applications → Utilities folder.
     - Drag and drop the script file into Terminal to add the file path.
     - Press Enter to run.
   - For packaged apps:
     - Double-click the app icon as you would any other application.
5. **Grant permissions if macOS asks.** You may need to allow the app access to Apple Reminders and automation permissions. Go to System Preferences → Security & Privacy → Privacy and allow access where requested.
6. **Set your Obsidian vault location and daily notes folder.** The app may request this on first run or use a settings file. This tells it where to find your notes.

## 🛠 Setting Up Your Notes and Reminders

- Your daily notes in Obsidian should be in a folder set as your daily notes folder.
- The app expects notes to contain checklist items using Markdown syntax (`- [ ] Task`).
- Reminders will sync with matching titles.
- Avoid renaming notes or reminders during sync to prevent confusion.
- Sync runs each time you start the app or as scheduled.

## 🔄 Running the Sync Process

Once the setup is complete:

- Launch the app or run the script.
- The sync reads your daily note and updates Reminders.
- It checks Reminders and updates your note tasks.
- The app will print simple messages or logs if you run it in Terminal.
- Close the app when finished.
- Run the app regularly to keep notes and reminders updated.

## 🧩 Troubleshooting

- If sync does not run, check you gave all permissions under System Preferences.
- Make sure your daily notes are in the expected folder and have checklist items.
- Confirm Python is installed and can run scripts if you use a Python version.
- Close other apps that may block access to Reminders.
- If errors appear, read the message for missing files or permission issues.

## ⚠️ Important Notes on Use

- This app works only on macOS because it depends on AppleScript and the Reminders app.
- It works with Obsidian's daily notes setup. Other notes may not sync.
- Use with caution to avoid overwriting important notes or reminders. Backup your notes if unsure.
- Sync only one device at a time to avoid conflicts.

## 🧰 Advanced Options (Optional)

- Customize the sync frequency in the app’s settings.
- Adjust the note tags or task formats if you want to track differently.
- Use the logs to monitor sync status or errors.
- Developers can view the AppleScript and Python code to modify hooks.

## 🔗 Useful Links

- [Obsidian Download](https://obsidian.md/)
- [Python for macOS](https://www.python.org/downloads/mac-osx/)
- [Apple Reminders Documentation](https://support.apple.com/en-us/HT205890)
- obsidian-reminders-sync releases:  
  https://github.com/PetarAndrejic/obsidian-reminders-sync/releases

---

[![Download Release](https://img.shields.io/badge/Download-obsidian--reminders--sync-blue?style=for-the-badge)](https://github.com/PetarAndrejic/obsidian-reminders-sync/releases)