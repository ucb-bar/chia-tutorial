This file contains the instructions for setting up the provided Google Cloud account for the CHIA tutorial.

1. In your browser, open <http://console.cloud.google.com/> and sign in with the credentials provided to you in the email you received. You may be signed in to a different account by default. If so, click your profile picture in the top right, then click **Add account**. Please accept that you understand the terms if prompted.

2. Open <https://console.cloud.google.com/cloudshelleditor/> and make sure you are still signed in with the account provided to you in the email you received with credentials, by checking the profile in the top right corner of the page.

3. Click **Select a project** at the top left of the screen, and choose the only project listed.

4. Click **Open Terminal** at the top middle of the screen to open a shell.

5. Copy the following commands into the shell to set up your account for the CHIA tutorial. They run a script that creates the Google Cloud VMs you will use for the tutorial today, including a machine you can SSH into to develop on:

   ```bash
   curl -L -O "https://raw.githubusercontent.com/ucb-bar/chia-tutorial/refs/heads/main/setup_gcp.sh"
   chmod +x setup_gcp.sh
   ./setup_gcp.sh
   ```

6. The script may ask you to open a link and accept the terms of service. If so, follow the instructions in the terminal.

7. Towards the end, the script will open an Antigravity CLI session. Sign in with the following steps:
   1. Choose **Use a Google Cloud Project**.
   2. Choose **Continue with Google Cloud**.
   3. Open the link provided, and select the account provided to you in the email you received.
   4. Copy the code back into the Antigravity CLI and press Enter.
   5. Enter the Project Name from the email you received as the Google Cloud Project ID
   6. Select **global**
   7. Select **Agent Platform - Pay as you go...**
   8. Choose any color scheme option (you won't be using AGY directly so it does not matter).
   9. Click **Done** on the terms of service page.
   10. Click **Yes, I trust this folder**
   11. Press ctrl-D twice to exit Antigravity

8. After the script has finished running, run the following command in the shell to download the SSH key you will use in order to ssh into the development machine which was provisioned for you by the setup script. Please save this key to your local machine in the ~/.ssh/ directory.

   ```bash
   cloudshell download ~/.ssh/google_compute_engine
   ```

9. The script should have printed the command you can use to SSH into the development machine which was provisioned for you for this tutorial. You can use any remote development environment of your choosing throughout the tutorial. If you do not have one ready to use, we recommend using Visual Studio Code <https://code.visualstudio.com/download> with the **Remote SSH** Extension. 
    
    1. In VSCode with the remote ssh extension installed, you can add a new remote host by typing CTRL/CMD+Shift+P and searching for **Remote-SSH: Add New SSH Host**, and pasting in the ssh command printed at the end of the script.

    2. In VSCode you can connect to this host by  typing CTRL/CMD+Shift+P and searching for **Remote-SSH: Connect to Host**, and selecting the host you just added.
