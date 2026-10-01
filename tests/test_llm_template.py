import importlib.util
import sys
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

from src.domain.query import QueryError
from src.llm.template import ChatTemplate, _to_json


MESSAGES = [{"role": "user", "content": "Count orders"}]
_HAS_JINJA = importlib.util.find_spec("jinja2") is not None


class FakeEnvironment:
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.filters = {}
        self.globals = {}
        self.variables = None
        self.output = "assistant:"
        self.instances.append(self)

    def from_string(self, source):
        return self

    def render(self, **kwargs):
        self.variables = kwargs
        return self.output


class TemplateContractTests(unittest.TestCase):
    def setUp(self):
        FakeEnvironment.instances = []
        modules = {
            "jinja2": SimpleNamespace(TemplateError=RuntimeError),
            "jinja2.ext": SimpleNamespace(Extension=object, loopcontrols=object),
            "jinja2.sandbox": SimpleNamespace(ImmutableSandboxedEnvironment=FakeEnvironment),
        }
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_flags_reach_render_not_an_ignored_formatter_kwargs(self):
        template = ChatTemplate("fixture", bos_token="B", eos_token="E")
        self.assertEqual(template.render(MESSAGES), "assistant:")
        environment = FakeEnvironment.instances[0]
        self.assertIs(environment.variables["enable_thinking"], False)
        self.assertIs(environment.variables["add_generation_prompt"], True)
        self.assertEqual(environment.variables["bos_token"], "B")
        self.assertEqual(environment.variables["eos_token"], "E")
        self.assertIsNone(environment.variables["tools"])
        self.assertEqual(environment.variables["messages"], MESSAGES)
        self.assertEqual(environment.filters["tojson"]("<текст>"), '"<текст>"')
        self.assertTrue(callable(environment.globals["raise_exception"]))
        self.assertEqual(len(environment.options["extensions"]), 2)

    def test_open_reasoning_prefix_is_an_error_instead_of_generating_sql_inside_it(self):
        template = ChatTemplate("fixture", bos_token="B", eos_token="E")
        environment = FakeEnvironment.instances[0]
        environment.output = "assistant\n<think>\n"
        with self.assertRaises(QueryError) as caught:
            template.render(MESSAGES)
        self.assertEqual(caught.exception.code, "chat_template_incompatible")
        environment.output = "assistant\n<think>\n\n</think>\n\n"
        self.assertEqual(template.render(MESSAGES), environment.output)

    def test_missing_template_and_nontext_messages_fail_closed(self):
        with self.assertRaises(QueryError) as caught:
            ChatTemplate(None, bos_token="B", eos_token="E")
        self.assertEqual(caught.exception.code, "chat_template_missing")
        template = ChatTemplate("fixture", bos_token="B", eos_token="E")
        for messages in (
            None, "text", {}, (), [], [None], ["text"], [1], [{}],
            [{"role": [], "content": "x"}], [{"role": None, "content": "x"}],
            [{"role": "user"}], [{"role": "user", "content": None}],
            [{"role": "user", "content": []}], [{"role": "tool", "content": "x"}],
        ):
            with self.subTest(messages=messages), self.assertRaises(QueryError) as caught:
                template.render(messages)
            self.assertEqual(caught.exception.code, "invalid_messages")

    def test_readonly_mapping_messages_are_accepted_without_mutation(self):
        template = ChatTemplate("fixture", bos_token="B", eos_token="E")
        message = MappingProxyType({"role": "user", "content": "{{ literal }}"})
        self.assertEqual(template.render([message]), "assistant:")
        self.assertIs(FakeEnvironment.instances[0].variables["messages"][0], message)

    def test_plain_json_filter_preserves_unicode_and_quotes(self):
        self.assertEqual(_to_json({"x": "O'Brien <1>"}), '{"x": "O\'Brien <1>"}')


@unittest.skipUnless(_HAS_JINJA, "Jinja2 is not installed; real template rendering requires it")
class JinjaRenderingTests(unittest.TestCase):
    def test_thinking_switch_closes_reasoning_in_template(self):
        source = (
            "{{ bos_token }}{% for m in messages %}{{ m.role }}:{{ m.content }}{{ eos_token }}{% endfor %}"
            "{% if add_generation_prompt %}assistant:"
            "{% if enable_thinking is defined and enable_thinking is false %}"
            "<think>\n\n</think>\n\n{% else %}<think>\n{% endif %}{% endif %}"
        )
        template = ChatTemplate(source, bos_token="B", eos_token="E")
        self.assertEqual(template.render(MESSAGES), "Buser:Count ordersEassistant:<think>\n\n</think>\n\n")

    def test_helpers_loopcontrols_generation_annotations_and_sandbox(self):
        source = (
            "{% for m in messages %}{% generation %}{{ m.content | tojson }}{% endgeneration %}"
            "{% break %}{% endfor %}{{ strftime_now('%Y') }}"
        )
        output = ChatTemplate(source, bos_token="", eos_token="E").render(MESSAGES)
        self.assertTrue(output.startswith('"Count orders"'))
        self.assertEqual(len(output[-4:]), 4)
        source = "{{ messages[0].__class__.__mro__ }}"
        with self.assertRaises(QueryError):
            ChatTemplate(source, bos_token="", eos_token="E").render(MESSAGES)


if __name__ == "__main__":
    unittest.main()
