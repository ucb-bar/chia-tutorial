This file contains the instructions for setting up the provided Google Cloud account for the CHIA tutorial.

1. In your browser, open <http://console.cloud.google.com/> and sign in with the credentials provided to you in the email you received. You may be signed in to a different account by default. If so, click your profile picture in the top right, then click **Add account**.

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
   5. 

8. Run the following command in the shell to download 
