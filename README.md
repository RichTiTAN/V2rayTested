# V2rayTested
This is a small app to check v2ray subscriptions, exctract healthy configs for your network interface and re-upload them to your custom github repository.  

# How to use
__You need the Python application to use this app__
1. Download app.py from this repository.
2. Download [xray-core](https://github.com/XTLS/Xray-core)
3. Put all the files into a single folder
4. Modify app.py in NotePad or any similar app and replace these lines to match your settings:
```
GITHUB_TOKEN = "INSERT-YOUR-GITHUB-TOKEN"
REPO_NAME = "USERNAME/REPO"
SUBSCRIPTIONS = [
    "SUB01",
    "SUB02",
    "SUB03",
    "SUB04"
]
```
5. Run
``` Python app.py ```
in terminal

To exit/cancel you can just close the app.
