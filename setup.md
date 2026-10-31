This file contains the instructions for setting up the provided Google Cloud account for the CHIA tutorial. We highly recommend using **Google Chrome** as your browser for this setup if possible.

1. In your browser, open <http://console.cloud.google.com/> and sign in with the credentials provided to you in the email you received. You may be signed in to a different account by default. If so, click your profile picture in the top right, then click **Add account**. Please accept that you understand the terms if prompted.

2. Open <https://console.cloud.google.com/cloudshelleditor/> and make sure you are still signed in with the account provided to you in the email you received with credentials, by checking the profile in the top right corner of the page. Please authorize the use of Cloud Shell.

3. Click **Select a project** at the top left of the screen, and choose the only project listed. Please then refresh the page.

4. Click **Open Terminal** at the top middle of the screen to open a shell. 

If you see the prompt

```bash
Welcome to Cloud Shell! Type "help" to get started.
To set your Cloud Platform project in this session use `gcloud config set project [PROJECT_ID]`.
You can view your projects by running `gcloud projects list`.
```

then please run the following command, filling in `[PROJECT_ID]` with the project id provided in the email to you:

```bash
gcloud config set project [PROJECT_ID]
```

You should then see the project id in the shell prefix, for example:

```bash
devstar1956@cloudshell:~ (chia-codesign26oak-1956)$ 
```

5. Copy the following commands into the shell to set up your account for the CHIA tutorial. They run a script that creates the Google Cloud VMs you will use for the tutorial today, including a machine you can SSH into to develop on:

   ```bash
   curl -L -O "https://raw.githubusercontent.com/ucb-bar/chia-tutorial/refs/heads/main/setup_gcp.sh"
   chmod +x setup_gcp.sh
   ./setup_gcp.sh
   ```

If the script exits immediately, having only printed "Using gcloud signed in as ..." and no line with "Using project ...", then please ensure that a project is selected at the top left (see step 3 and 4), and then refresh the page.

6. The script may ask you to open a link and accept the terms of service. If so, follow the instructions in the terminal.

7. Towards the end, the script will open an Antigravity CLI session. Sign in with the following steps:
   1. Choose **Use a Google Cloud Project**.
   2. Choose **Continue with Google Cloud**.
   3. Open the link provided, and select the account provided to you in the email you received.
   4. Copy the code back into the Antigravity CLI and press Enter.
   5. If asked, choose **Pay As You Go – Pay only for the tokens you use, billed through Gemini platform.**
   6. Enter the Project Name from the email you received as the Google Cloud Project ID
   7. Select **global**
   8. If asked now, select **Agent Platform - Pay as you go...**
   9. Choose any color scheme option (you won't be using AGY directly so it does not matter).
   10. Click **Done** on the terms of service page.
   11. Click **Yes, I trust this folder**
   12. Press ctrl-D twice to exit Antigravity

8. After the script has finished running, run the following command in the shell to download the SSH key you will use to SSH into the development machine the setup script provisioned for you:

   ```bash
   cloudshell download ~/.ssh/google_compute_engine
   ```

   Save the key anywhere on your laptop and **note the path where you saved it**; you will need it in the next step. If you already have a `~/.ssh/google_compute_engine` file, don't overwrite it: save this one somewhere else, or under a different name such as `~/.ssh/chia-tutorial-key`. Also restrict the key's permissions, or SSH will refuse to use it.

For MacOS or Linux:
   ```bash
   chmod 600 <absolute path to the key>
   ```

For Windows **using PowerShell**
   ```powershell
   $key = "<path_to_key>"
   icacls $key /reset
   icacls $key /inheritance:r
   icacls $key /grant:r "$($env:USERNAME):(R)"
   ```


10. The script should have printed the command you can use to SSH into the development machine which was provisioned for you for this tutorial. It looks like `ssh -o IdentitiesOnly=yes -i <absolute path to the key> USER@IP`: replace `<absolute path to the key>` with the absolute path where you saved the key in step 8. You can use any remote development environment of your choosing throughout the tutorial. If you do not have one ready to use, we recommend using Visual Studio Code <https://code.visualstudio.com/download> with the **Remote SSH** Extension. 
    
    1. In VSCode with the remote ssh extension installed, you can add a new remote host by typing CTRL/CMD+Shift+P and searching for **Remote-SSH: Add New SSH Host**, and pasting in that ssh command (with your key's absolute path filled in).

    2. In VSCode you can connect to this host by  typing CTRL/CMD+Shift+P and searching for **Remote-SSH: Connect to Host**, and selecting the host you just added.
