import os
from google_auth_oauthlib.flow import InstalledAppFlow

# The scopes required by the automation script to send emails and upload to drive.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
]

def main():
    print("Looking for credentials.json...")
    if not os.path.exists("credentials.json"):
        print("ERROR: credentials.json not found in this directory.")
        print("You must download your OAuth 2.0 Client ID JSON from Google Cloud Console,")
        print("rename it to 'credentials.json', and place it in this folder before running this script.")
        return

    print("Starting authentication flow...")
    print("A browser window should open. Please log in with the Gmail account you want to use.")
    
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    # This will open a local web server to receive the callback from Google
    creds = flow.run_local_server(port=0)
    
    # Save the generated token
    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())
        
    print("\nSUCCESS! token.json has been generated.")
    print("You can now open token.json, copy its contents, and paste them into the GMAIL_TOKEN secret on GitHub.")

if __name__ == "__main__":
    main()
