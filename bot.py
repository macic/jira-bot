import appeal
from stats import JiraStatusAnalyzer
from config import server, email, api_token

app = appeal.Appeal()
analyzer  = JiraStatusAnalyzer(server=server, email=email, api_token=api_token)

@app.command()
def get_myself():
    print(analyzer.myself)

app.main()