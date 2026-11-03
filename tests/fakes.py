class FakeAI:
    """Stands in for AIClient: returns queued responses and records prompts."""

    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = 0
        self.prompts = []

    def complete_json(self, system, user):
        self.calls += 1
        self.prompts.append(user)
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else {}


class FakeTavily:
    def __init__(self, hits=()):
        self.hits = list(hits)
        self.queries = []
        self.include_domains = []

    def search(self, query, include_domains):
        self.queries.append(query)
        self.include_domains.extend(include_domains)
        return self.hits
