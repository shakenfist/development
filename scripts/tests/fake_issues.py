"""A stateful stand-in for GitHub's issue tracker, for the issue tests.

audit-manage-issues.py files and closes issues across the whole fleet,
so its tests need to see what it would have done to a repository's
issues, not merely which functions it called. This fake answers the
`gh` invocations the issue scripts make -- listing, creating, editing,
commenting on and closing issues, and resolving a repository's
canonical name -- out of an in-memory tracker, and records every write.

`FakeGitHub` in audit/github.py scripts one response per argument
list, which suits a check asking one fixed question. The issue scripts
ask questions whose answers depend on what they did a moment earlier,
which is what this keeps track of.
"""

import json

from audit.github import CompletedCommand, GitHubClient


def _options(args):
    """The `--flag value` pairs of an argument list, and its positionals."""
    options = {}
    positional = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith('--') and index + 1 < len(args):
            options[arg] = args[index + 1]
            index += 2
        else:
            positional.append(arg)
            index += 1
    return options, positional


class FakeIssueTracker(GitHubClient):
    """Open and closed issues for any number of repositories.

    `fail` names the operations that should fail, as the second word
    of the `gh issue` invocation ('list', 'create', 'edit', 'comment',
    'close'). `writes` records each successful write as an
    (operation, repository, title-or-number) tuple, in order.
    """

    def __init__(self, fail=()):
        self.issues = []
        self.writes = []
        self.calls = []
        self.fail = set(fail)
        self.next_number = 1

    def add(self, repo, title, body='', label='consistency', state='open'):
        """Seed an issue, returning its number."""
        number = self.next_number
        self.next_number += 1
        self.issues.append({'repo': repo, 'number': number, 'title': title,
                            'body': body, 'labels': [label], 'state': state})
        return number

    def open_titles(self, repo):
        return sorted(i['title'] for i in self.issues
                      if i['repo'] == repo and i['state'] == 'open')

    def issue(self, number):
        return next(i for i in self.issues if i['number'] == number)

    def run(self, args, timeout=30):
        args = list(args)
        self.calls.append(args)
        if args[:1] == ['api']:
            # Canonical name resolution: nothing here is renamed.
            return CompletedCommand(stdout=args[1].split('/', 1)[1] + '\n')
        if args[:1] == ['label']:
            return CompletedCommand()
        if args[:1] != ['issue']:
            return CompletedCommand(returncode=1, stderr=f'unexpected {args}')

        operation = args[1]
        options, positional = _options(args[2:])
        repo = options['--repo']
        if operation in self.fail:
            return CompletedCommand(returncode=1, stderr=f'{operation} failed')

        if operation == 'list':
            return self._list(repo, options)
        if operation == 'create':
            number = self.add(repo, options['--title'], options['--body'],
                              options['--label'])
            self.writes.append(('create', repo, options['--title']))
            return CompletedCommand(
                stdout=f'https://github.com/{repo}/issues/{number}\n')

        number = int(positional[0])
        issue = self.issue(number)
        if operation == 'edit':
            issue['body'] = options['--body']
        elif operation == 'close':
            issue['state'] = 'closed'
        elif operation != 'comment':
            return CompletedCommand(returncode=1, stderr=f'unexpected {args}')
        self.writes.append((operation, repo, number))
        return CompletedCommand()

    def _list(self, repo, options):
        """Answer `gh issue list` the way GitHub would.

        A `--search` is matched loosely, as GitHub's search does: a
        quoted phrase finds every title containing it, which is why
        "Workflow standards" also finds "Workflow standards
        (flake8wrap)". `--limit` truncates, and newest comes first.
        """
        matched = [i for i in self.issues
                   if i['repo'] == repo and i['state'] == options['--state']
                   and options['--label'] in i['labels']]
        search = options.get('--search')
        if search is not None:
            phrase = search.replace(' in:title', '').strip('"').lower()
            matched = [i for i in matched if phrase in i['title'].lower()]
        matched.sort(key=lambda i: -i['number'])
        matched = matched[:int(options['--limit'])]
        fields = options['--json'].split(',')
        return CompletedCommand(stdout=json.dumps(
            [{field: i[field] for field in fields} for i in matched]))
