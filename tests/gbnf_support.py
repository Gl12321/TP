import ast
from collections import deque
import re


_TOKENS = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\[(?:[^\]\\]|\\.)*\]|[A-Za-z][A-Za-z0-9-]*|[()|*+?])')


class GrammarRecognizer:
    def __init__(self, source: str):
        self.rules: dict[str, list[tuple]] = {}
        self.counter = 0
        for line in source.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, body = line.partition("::=")
            if not separator or name.strip() in self.rules:
                raise ValueError(f"Invalid/duplicate rule: {line}")
            self.tokens = []
            cursor = 0
            while cursor < len(body):
                match = _TOKENS.match(body, cursor)
                if match is None:
                    if not body[cursor:].strip():
                        break
                    raise ValueError(f"Unsupported GBNF at {body[cursor:]!r}")
                self.tokens.append(match[1])
                cursor = match.end()
            self.position = 0
            alternatives = self._alternatives()
            if self.position != len(self.tokens):
                raise ValueError(f"Unparsed grammar: {body}")
            self.rules[name.strip()] = alternatives
        self.rules["$start"] = [("root",)]
        references = {item for choices in self.rules.values() for rhs in choices for item in rhs if isinstance(item, str)}
        missing = references - self.rules.keys()
        if missing:
            raise ValueError(f"Undefined rules: {missing}")
        self.nullable: set[str] = set()
        changed = True
        while changed:
            changed = False
            for name, alternatives in self.rules.items():
                if name not in self.nullable and any(all(isinstance(item, str) and item in self.nullable for item in rhs) for rhs in alternatives):
                    self.nullable.add(name)
                    changed = True

    def _peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _fresh(self, alternatives: list[tuple]) -> str:
        self.counter += 1
        name = f"${self.counter}"
        self.rules[name] = alternatives
        return name

    def _alternatives(self) -> list[tuple]:
        alternatives = [self._sequence()]
        while self._peek() == "|":
            self.position += 1
            alternatives.append(self._sequence())
        return alternatives

    def _sequence(self) -> tuple:
        symbols = []
        while self._peek() not in {None, ")", "|"}:
            token = self.tokens[self.position]
            self.position += 1
            if token == "(":
                symbol = self._fresh(self._alternatives())
                if self._peek() != ")":
                    raise ValueError("Missing group terminator")
                self.position += 1
            elif token.startswith('"'):
                text = ast.literal_eval(token)
                symbol = self._fresh([tuple(("char", character) for character in text)])
            elif token.startswith("["):
                re.compile(token)
                symbol = ("class", token)
            elif token in {"*", "+", "?"}:
                raise ValueError("Repetition without an expression")
            else:
                symbol = token
            modifier = self._peek()
            if modifier in {"*", "+", "?"}:
                self.position += 1
                repeated = self._fresh([])
                if modifier == "?":
                    self.rules[repeated] = [(), (symbol,)]
                elif modifier == "*":
                    self.rules[repeated] = [(), (symbol, repeated)]
                else:
                    self.rules[repeated] = [(symbol,), (symbol, repeated)]
                symbol = repeated
            symbols.append(symbol)
        return tuple(symbols)

    def accepts(self, text: str) -> bool:
        chart: list[set[tuple]] = [set() for _ in range(len(text) + 1)]
        chart[0].add(("$start", ("root",), 0, 0))
        for index, states in enumerate(chart):
            agenda = deque(states)

            def add(state: tuple) -> None:
                if state not in states:
                    states.add(state)
                    agenda.append(state)

            while agenda:
                name, rhs, dot, start = agenda.popleft()
                if dot == len(rhs):
                    for parent, items, position, origin in tuple(chart[start]):
                        if position < len(items) and items[position] == name:
                            add((parent, items, position + 1, origin))
                    continue
                symbol = rhs[dot]
                if isinstance(symbol, str):
                    for choice in self.rules[symbol]:
                        add((symbol, choice, 0, index))
                    if symbol in self.nullable:
                        add((name, rhs, dot + 1, start))
                elif index < len(text):
                    kind, pattern = symbol
                    matched = text[index] == pattern if kind == "char" else re.fullmatch(pattern, text[index]) is not None
                    if matched:
                        chart[index + 1].add((name, rhs, dot + 1, start))
        return ("$start", ("root",), 1, 0) in chart[-1]
