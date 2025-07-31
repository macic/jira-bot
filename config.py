import os

server = 'https://adverity.atlassian.net'
email = 'radoslaw.jeruzal@adverity.com'
api_token = os.environ.get('JIRA_API_TOKEN')

filter = """
project IN (CN, BRB)
AND type = Bug
AND created >= -24w
AND status NOT IN (Done, "Done w/o dev", "Dev Ready/Complete", Closed)
AND "product domain[dropdown]" = Connectivity
"""
