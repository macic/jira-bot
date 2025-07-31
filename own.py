import os

from jira import JIRA
from datetime import datetime
from collections import defaultdict
import pytz
import sys
from typing import List, Dict, Optional, Any
import json

class JiraStatusAnalyzer:
    # Define the expected status flow - updated with all CN project statuses
    EXPECTED_STATUSES = [
        'New',
        'New Issues',
        'Ready For Specification',
        'Product specification', 
        'Refinement',
        'Ready to Plan',
        'Ready for Development',
        'Missing information',
        'In Progress',
        'Waiting for Code Review',
        'Code Review',
        'Ready for QA',
        'In Testing',
        'Testing Complete',
        'Passed',
        'Failed',
        'Dev Ready/Complete',
        'Done w/o dev',
        'Done',
        'On Hold'
    ]

    def __init__(self, server: str, email: str, api_token: str):
        """Initialize the JiraStatusAnalyzer with JIRA credentials."""
        self.jira = JIRA(server=server, basic_auth=(email, api_token))
        self.myself = self.jira.myself()
        print(f"Logged in as: {self.myself['displayName']} ({self.myself['emailAddress']})")

    def get_issues_by_jql(self, jql_query: str, max_results_per_page: int = 100, custom_field_id: str = 'customfield_10476') -> List[Dict]:
        """Fetch all issues based on JQL query using pagination."""
        all_issues = []
        start_at = 0
        
        # First get the total count using enhanced search
        try:
            count_response = self.jira.enhanced_search_issues(
                jql_query,
                maxResults=0,  # This will only return the count
                fields=['summary']  # Minimal fields for count
            )
            total_issues = count_response.total
            print(f"Found {total_issues} issues in total")
        except Exception as e:
            print(f"Error getting total count: {str(e)}")
            if hasattr(e, 'response'):
                print(f"Response status: {e.response.status_code if hasattr(e.response, 'status_code') else 'N/A'}")
                print(f"Response text: {e.response.text if hasattr(e.response, 'text') else 'N/A'}")
            return all_issues
        
        while True:
            try:
                # Use direct REST API call for pagination
                url = f"{self.jira._options['server']}/rest/api/3/search"
                params = {
                    'jql': jql_query,
                    'startAt': start_at,
                    'maxResults': max_results_per_page,
                    'expand': 'changelog',
                    'fields': f'summary,status,{custom_field_id},created'
                }
                response = self.jira._session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                # Convert the JSON response to JIRA issue objects
                issues_page = [self.jira.issue(raw['key'], expand='changelog') for raw in data['issues']]
                
                # Add issues from this page to our results
                all_issues.extend(issues_page)
                print(f"Fetched {len(all_issues)} of {total_issues} issues...")
                
                # If we've fetched all issues, break
                if len(all_issues) >= total_issues:
                    break
                    
                # Move to the next page
                start_at += max_results_per_page
                
            except Exception as e:
                print(f"Error fetching issues: {str(e)}")
                if hasattr(e, 'response'):
                    print(f"Response status: {e.response.status_code if hasattr(e.response, 'status_code') else 'N/A'}")
                    print(f"Response text: {e.response.text if hasattr(e.response, 'text') else 'N/A'}")
                break
        
        return all_issues

    def _get_status_changes(self, issue) -> List[Dict]:
        """Extract status changes from an issue's changelog."""
        status_changes = []
        for history in issue.changelog.histories:
            for item in history.items:
                if item.field == 'status':
                    status_changes.append({
                        'from_status': item.fromString if item.fromString else 'None',
                        'to_status': item.toString if item.toString else 'None',
                        'timestamp': datetime.strptime(history.created, '%Y-%m-%dT%H:%M:%S.%f%z')
                    })
        return sorted(status_changes, key=lambda x: x['timestamp'])

    def _calculate_status_durations(self, status_changes: List[Dict]) -> Dict[str, float]:
        """Calculate time spent in each status for a given list of status changes."""
        status_durations = defaultdict(float)
        if not status_changes:
            return status_durations

        current_status = 'New Issues'
        current_time = status_changes[0]['timestamp']

        for change in status_changes:
            duration = (change['timestamp'] - current_time).total_seconds() / 3600
            status_durations[current_status] += duration
            current_status = change['to_status']
            current_time = change['timestamp']

        # If not in Done status, calculate time until now
        if current_status != 'Done':
            now = datetime.now(pytz.UTC)
            duration = (now - current_time).total_seconds() / 3600
            status_durations[current_status] += duration

        return status_durations

    def analyze_single_issue(self, issue_key: str) -> Dict[str, float]:
        """Analyze status durations for a single issue."""
        issue = self.jira.issue(issue_key, expand='changelog')
        status_changes = self._get_status_changes(issue)
        return self._calculate_status_durations(status_changes)

    def analyze_multiple_issues(self, jql_query: str, max_results_per_page: int = 100, 
                               custom_field_id: str = 'customfield_10476', 
                               grouping_field_name: str = 'Idea Category') -> Dict[str, Dict[str, Any]]:
        """Analyze status durations for all issues based on JQL query."""
        print(f"\nFetching issues with query: {jql_query}")
        try:
            issues = self.get_issues_by_jql(jql_query, max_results_per_page, custom_field_id)
        except Exception as e:
            print(f"Error fetching issues: {str(e)}")
            return {}
            
        results = {}
        
        print("Analyzing issues...")
        for i, issue in enumerate(issues, 1):
            if i % 100 == 0:  # Progress indicator
                print(f"Analyzed {i} issues...")
                
            try:
                status_changes = self._get_status_changes(issue)
                durations = self._calculate_status_durations(status_changes)
                
                # Include custom field value in results - use the configurable field
                custom_field_value = getattr(issue.fields, custom_field_id, None)
                
                results[issue.key] = {
                    'durations': durations,
                    'summary': issue.fields.summary,
                    'grouping_field_value': custom_field_value,
                    'grouping_field_name': grouping_field_name,
                    'current_status': issue.fields.status.name,
                    'created': issue.fields.created
                }
            except Exception as e:
                print(f"Error processing issue {issue.key}: {str(e)}")
                continue
        
        print(f"Analysis complete for {len(results)} issues")
        return results

    def print_status_durations(self, durations: Dict[str, float], issue_key: Optional[str] = None):
        """Print status durations in a formatted way."""
        if issue_key:
            print(f"\nTime spent in each status for issue {issue_key}:")
        else:
            print("\nTime spent in each status:")

        total_hours = 0.0
        for status in self.EXPECTED_STATUSES:
            hours = durations.get(status, 0.0)
            total_hours += hours
            days = int(hours // 24)
            remaining_hours = hours % 24
            if days > 0:
                print(f"{status}: {days} days and {remaining_hours:.1f} hours")
            else:
                print(f"{status}: {hours:.1f} hours")

        # Print total time
        total_days = int(total_hours // 24)
        total_remaining_hours = total_hours % 24
        print("\nTotal time:")
        if total_days > 0:
            print(f"Total: {total_days} days and {total_remaining_hours:.1f} hours")
        else:
            print(f"Total: {total_hours:.1f} hours")

        # Print any unexpected statuses
        unexpected_statuses = set(durations.keys()) - set(self.EXPECTED_STATUSES)
        if unexpected_statuses:
            print("\nNote: The following unexpected statuses were also found:")
            for status in unexpected_statuses:
                hours = durations[status]
                total_hours += hours  # Add unexpected status time to total
                days = int(hours // 24)
                remaining_hours = hours % 24
                if days > 0:
                    print(f"{status}: {days} days and {remaining_hours:.1f} hours")
                else:
                    print(f"{status}: {hours:.1f} hours")
            
            # Print updated total including unexpected statuses
            if unexpected_statuses:
                total_days = int(total_hours // 24)
                total_remaining_hours = total_hours % 24
                print("\nTotal time (including unexpected statuses):")
                if total_days > 0:
                    print(f"Total: {total_days} days and {total_remaining_hours:.1f} hours")
                else:
                    print(f"Total: {total_hours:.1f} hours")

    def _format_time(self, hours: float) -> str:
        """Format time in hours to days and hours string."""
        days = int(hours // 24)
        remaining_hours = hours % 24
        if days > 0:
            return f"{days} days and {remaining_hours:.1f} hours"
        return f"{hours:.1f} hours"

    def print_aggregated_results(self, results: Dict[str, Dict[str, Any]]):
        """Print aggregated results for multiple issues."""
        if not results:
            print("No issues found matching the query.")
            return

        # Get the grouping field name from the first result
        grouping_field_name = next(iter(results.values()))['grouping_field_name'] if results else 'Category'

        # Calculate averages and totals for each status
        aggregated = defaultdict(lambda: {'total_hours': 0.0, 'count': 0})
        grand_total_hours = 0.0
        
        # Group issues by the specified field and calculate category-specific stats
        category_stats = defaultdict(lambda: {
            'total_hours': 0.0,
            'count': 0,
            'issues': [],
            'status_totals': defaultdict(float)
        })
        
        for issue_key, issue_data in results.items():
            issue_total = 0.0
            category = issue_data['grouping_field_value'] or 'Not Set'
            
            # Calculate total time for this issue
            for status, hours in issue_data['durations'].items():
                aggregated[status]['total_hours'] += hours
                aggregated[status]['count'] += 1
                category_stats[category]['status_totals'][status] += hours
                issue_total += hours
            
            # Update category statistics
            category_stats[category]['total_hours'] += issue_total
            category_stats[category]['count'] += 1
            category_stats[category]['issues'].append(issue_key)
            grand_total_hours += issue_total

        print(f"\nAggregated results for {len(results)} issues:")

        # Print Time to Market summary first
        print(f"\nTime to Market by {grouping_field_name}:")
        print("-" * 80)
        for category, stats in sorted(category_stats.items(), key=lambda x: x[1]['total_hours'], reverse=True):
            avg_hours = stats['total_hours'] / stats['count'] if stats['count'] > 0 else 0
            print(f"{category}: {self._format_time(avg_hours)} ({stats['count']} issues)")
        print("-" * 80)

        # Print field breakdown
        print(f"\nBreakdown by {grouping_field_name}:")
        print("-" * 80)
        for category, stats in sorted(category_stats.items(), key=lambda x: x[1]['total_hours'], reverse=True):
            avg_hours = stats['total_hours'] / stats['count'] if stats['count'] > 0 else 0
            
            print(f"\n{category} ({stats['count']} issues):")
            print(f"  Total time: {self._format_time(stats['total_hours'])}")
            print(f"  Time to Market: {self._format_time(avg_hours)}")
            
            # Print status breakdown for this category
            print("  Status breakdown:")
            for status in self.EXPECTED_STATUSES:
                if status in stats['status_totals']:
                    status_hours = stats['status_totals'][status]
                    print(f"    {status}: {self._format_time(status_hours)}")
            
            # Print issues in this category (limited to 10)
            print("  Issues:")
            for issue_key in stats['issues'][:10]:  # Limit to 10 issues
                issue_data = results[issue_key]
                print(f"    {issue_key}: {issue_data['summary']} (Current: {issue_data['current_status']})")
            if len(stats['issues']) > 10:
                print(f"    ... and {len(stats['issues']) - 10} more issues")
            print("-" * 80)

        # Print overall status durations
        print("\nOverall time spent in each status:")
        for status in self.EXPECTED_STATUSES:
            if status in aggregated:
                avg_hours = aggregated[status]['total_hours'] / aggregated[status]['count']
                total_hours = aggregated[status]['total_hours']
                print(f"{status}:")
                print(f"  Total: {self._format_time(total_hours)}")
                print(f"  Average: {self._format_time(avg_hours)}")

        # Print grand total
        avg_hours = grand_total_hours / len(results) if results else 0
        print("\nGrand Total:")
        print(f"Total time across {len(results)} issues: {self._format_time(grand_total_hours)}")
        print(f"Average Time to Market: {self._format_time(avg_hours)}")

    def run_analysis(self, jql_query: str, grouping_mode: str = 'impact', max_results_per_page: int = 100):
        """
        Run analysis with the specified grouping mode.
        
        Args:
            jql_query: The JQL query to fetch issues
            grouping_mode: Either 'impact' or 'idea_category'
            max_results_per_page: Number of results per page for pagination
        """
        # Configuration options for grouping
        GROUPING_OPTIONS = {
            'impact': {
                'field_id': 'customfield_10068',  # Impact field (High/Medium/Low)
                'field_name': 'Impact',
                'description': 'Group by Impact level (Low, Medium, High)'
            },
            'idea_category': {
                'field_id': 'customfield_10476',
                'field_name': 'Idea Category', 
                'description': 'Group by Idea Category'
            }
        }
        
        if grouping_mode not in GROUPING_OPTIONS:
            print(f"Error: Invalid grouping mode '{grouping_mode}'. Available modes: {list(GROUPING_OPTIONS.keys())}")
            return
            
        config = GROUPING_OPTIONS[grouping_mode]
        print(f"Using grouping mode: {grouping_mode} - {config['description']}")
        
        # Run the analysis
        results = self.analyze_multiple_issues(
            jql_query, 
            max_results_per_page=max_results_per_page,
            custom_field_id=config['field_id'],
            grouping_field_name=config['field_name']
        )
        
        # Print results
        self.print_aggregated_results(results)
        
        return results

# Example usage
if __name__ == "__main__":
    # Initialize the analyzer
    analyzer = JiraStatusAnalyzer(
        server='https://adverity.atlassian.net',
        email='radoslaw.jeruzal@adverity.com',
        api_token=os.environ.get('JIRA_API_TOKEN')
    )

    # Check for command line arguments
    grouping_mode = 'idea_category'  # Default mode changed to idea_category as requested
    if len(sys.argv) > 1:
        if sys.argv[1] in ['impact', 'idea_category']:
            grouping_mode = sys.argv[1]
        else:
            print("Usage: python own.py [impact|idea_category]")
            print("  impact       - Group by Impact level (Low, Medium, High)")
            print("  idea_category - Group by Idea Category")
            sys.exit(1)

    # Example: Analyze non-bug issues with the chosen grouping (2025 only)
    jql_query = "project = CN AND created >= '2024-01-01' AND status='Done' AND type != 'Bug' ORDER BY created DESC"
    
    print(f"\n{'='*60}")
    print(f"JIRA BUG FIX TIME ANALYSIS")
    print(f"{'='*60}")
    print(f"Query: {jql_query}")
    print(f"Mode: {grouping_mode}")
    print(f"{'='*60}")
    
    results = analyzer.run_analysis(jql_query, grouping_mode)
    
    # Show usage examples
    print(f"\n{'='*60}")
    print("USAGE EXAMPLES:")
    print(f"{'='*60}")
    print("# Group by Impact (High/Medium/Low):")
    print("python own.py impact")
    print("")
    print("# Group by Idea Category:")
    print("python own.py idea_category")
    print("")
    print("# Or modify the script to use different modes:")
    print("analyzer.run_analysis(jql_query, 'impact')")
    print("analyzer.run_analysis(jql_query, 'idea_category')")
    print(f"{'='*60}")