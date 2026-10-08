# Stage 0 — Machine setup (Windows + WSL)

**Time:** about 1–1.5 hours, mostly waiting for downloads.
**Goal:** every tool the project needs, installed in one place and verified by a script.

## Why WSL

KX now ships kdb+ as **KDB-X**, and KDB-X **does not run natively on Windows**. It runs on Linux. Windows has a built-in way to run a real Ubuntu Linux system alongside Windows: **WSL** (Windows Subsystem for Linux).

So the whole project runs inside Ubuntu on WSL: q, the C++ compiler and Python. This has three benefits:

1. **One environment.** All three languages run on the same system, so they can talk to each other without Windows/Linux mismatches.
2. **It matches industry.** kdb+ systems at banks and funds run on Linux servers.
3. **It closes your Linux and command-line gap**, which matters for nearly every quant job.

You'll still edit code in a normal window (VS Code), and the dashboard opens in your normal Windows browser.

**Where things live**

| What | Where | Why |
|---|---|---|
| Code (this repo) | `C:\Users\Sunny\Desktop\System` (inside Ubuntu: `/mnt/c/Users/Sunny/Desktop/System`) | Visible in Windows Explorer and to Claude |
| kdb+ data (tick logs, historical database) | `~/kdbdata` inside Ubuntu | Much faster disk access than the Windows drive |

---

## Step 1 — Install WSL and Ubuntu

1. Click Start, type **PowerShell**, right-click it, and choose **Run as administrator**.
2. Run:
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
   This turns on the Windows features WSL needs and downloads Ubuntu 24.04, the current long-term-support version.
3. **Restart the computer** when asked.
4. After the restart, an Ubuntu window opens by itself. If it doesn't, open **Ubuntu** from the Start menu. Wait for it to finish installing.
5. Choose a Linux **username** (lowercase, e.g. `sunny`) and a **password**.
   - The password doesn't show any characters while you type. That's normal.
   - You'll need this password for `sudo` (admin) commands, so remember it.

**If it fails:** an error mentioning "virtualization" means it's switched off in your BIOS. Send me the error message and your laptop model.

From now on, **"the terminal" means the Ubuntu window**. All remaining commands go there, not in PowerShell.

> **Terminal basics:** paste with **right-click** or **Ctrl+Shift+V** (Ctrl+V alone doesn't paste). `sudo` means "run as admin" and asks for your password. A line ending in `$` is the prompt waiting for your command.

## Step 2 — Update Ubuntu and install build tools

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential cmake git curl unzip rlwrap
```

What these do:
- `apt` is Ubuntu's package manager, like an app store run from the terminal. `update` refreshes the package list, and `upgrade` installs pending updates.
- `build-essential` is the **g++** C++ compiler and related tools. It's the Linux counterpart of Visual Studio's compiler.
- `cmake` is the standard build system for C++ projects.
- `git` is version control. This is the Linux copy, separate from your Windows Git.
- `curl` and `unzip` download and unpack files. The kdb+ installer needs both.
- `rlwrap` gives the q console arrow-key history, which q lacks on its own.

## Step 3 — Install KDB-X (kdb+)

1. In your Windows browser, go to **developer.kx.com** and register for **KDB-X Community Edition** (free).
2. **Read the licence terms** while you sign up. The Community Edition is free, including for commercial use, but has limits: about 16 GB memory, 4 worker threads and 8 connections per q process. That's enough for this project. Check what it says about publishing benchmarks, because we planned to publish q query timings.
3. After you log in, the install page shows a **ready-made install command** containing your licence key. It starts with `curl -sSLO ... install_kdb.sh`. Copy the whole command.
4. Paste it into the terminal and press Enter. Accept the defaults. KDB-X installs into `~/.kx`.
5. **Close the Ubuntu window and open a new one**, so the terminal picks up the new `q` command.
6. Test it:
   ```bash
   q
   ```
   You should see a banner with the KDB-X version, then a `q)` prompt. Type:
   ```q
   1+1
   ```
   It should print `2`. Leave q with:
   ```q
   \\
   ```
   That's two backslashes.
7. Make q use `rlwrap` for arrow-key history:
   ```bash
   echo "alias q='rlwrap -r q'" >> ~/.bashrc && source ~/.bashrc
   ```
   `~/.bashrc` is a settings file that runs every time a terminal opens. This line makes typing `q` actually run `rlwrap q`.

## Step 4 — Install Python (Miniconda) inside Ubuntu

Your Windows Anaconda can't be used from Linux, so Ubuntu needs its own Python. Miniconda is the small version of Anaconda.

```bash
cd ~
curl -sSLO https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p ~/miniconda3
~/miniconda3/bin/conda init bash
rm Miniconda3-latest-Linux-x86_64.sh
```

What these do:
- `cd ~` moves to your Linux home folder.
- `curl -sSLO` downloads the installer.
- `bash ... -b -p ~/miniconda3` runs it silently (`-b`) into `~/miniconda3` (`-p`).
- `conda init bash` makes conda available every time a terminal opens.
- `rm` deletes the installer.

**Close and reopen the terminal.** The prompt should now start with `(base)`.

## Step 5 — Install VS Code and connect it to Ubuntu

1. On Windows, install **Visual Studio Code** from code.visualstudio.com, if you don't already have it.
2. In VS Code, open Extensions (Ctrl+Shift+X) and install:
   - **WSL** (Microsoft). Lets VS Code edit and run code inside Ubuntu.
   - **Python** (Microsoft).
   - **C/C++** (Microsoft).
   - **CMake Tools** (Microsoft).
   - **kdb** (KX). Adds q syntax highlighting and lets you run q code from the editor.
3. In the terminal, open the project:
   ```bash
   cd /mnt/c/Users/Sunny/Desktop/System
   code .
   ```
   `/mnt/c/` is how Ubuntu sees your Windows C: drive. `code .` opens the current folder (`.`) in VS Code. The bottom-left corner of VS Code should say **WSL: Ubuntu-24.04**.

## Step 6 — Create the project's Python environment

In the terminal, still in the project folder:

```bash
conda env create -f environment.yml
conda activate oak
echo "conda activate oak" >> ~/.bashrc
```

What these do:
- `conda env create` reads `environment.yml` and installs exactly the packages the project needs into a separate environment called `oak` (short for options-analytics-kdb). It uses the free conda-forge channel.
- `conda activate oak` switches to that environment. The prompt changes to `(oak)`.
- The last line activates `oak` automatically in every new terminal.

This step takes a few minutes.

## Step 7 — Set up Git and GitHub inside Ubuntu

```bash
git config --global user.name "Sunny Alex"
git config --global user.email "sunnyalex1234@gmail.com"
git config --global init.defaultBranch main
git config --global core.autocrlf input
sudo apt install -y gh
gh auth login
```

- The first three lines set your name and email on commits, and name the main branch `main`.
- `core.autocrlf input` stops Windows/Linux line-ending differences from making every file look changed. The repo's `.gitattributes` enforces the same rule.
- `gh` is GitHub's command-line tool. When `gh auth login` asks: choose **GitHub.com**, then **HTTPS**, then **Yes** to authenticate Git, then **Login with a web browser**. Copy the code it shows, press Enter, and approve in your browser.

Don't create the repo yet. We'll do the first commit together in Stage 0, so you learn the Git commands as we go.

## Step 8 — Make the data folder and run the checker

```bash
mkdir -p ~/kdbdata
cd /mnt/c/Users/Sunny/Desktop/System
bash scripts/check_setup.sh
```

- `mkdir -p ~/kdbdata` creates the folder where kdb+ will store tick logs and the historical database.
- The checker tests every tool: g++, cmake, git, q, each Python package, and a live call to Deribit for the current BTC index price.

Every line should say **PASS**. If anything says **FAIL**, the line under it says what to do. Otherwise, send me the full output.

---

## Daily start-up (once setup is done)

1. Open **Ubuntu** from the Start menu.
2. `cd /mnt/c/Users/Sunny/Desktop/System`
3. `code .`

The `oak` environment activates by itself.
