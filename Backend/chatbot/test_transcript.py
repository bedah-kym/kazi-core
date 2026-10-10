"""History handed to the model keeps real roles and whole messages."""
from django.test import SimpleTestCase

from chatbot.transcript import build_history_messages, has_several_speakers, strip_wake_word

ASSISTANT_REPLY = (
    "I'll create the folder and file.\n"
    "Step 2: write the three lines\n"
    "command: mkdir qa_audit\n"
    "\n"
    "Done."
)


def _tool_ids(messages):
    """Every tool_use id and every tool_result id the built history carries."""
    uses, results = set(), set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                uses.add(block["id"])
            elif block.get("type") == "tool_result":
                results.add(block["tool_use_id"])
    return uses, results


class BuildHistoryMessagesTests(SimpleTestCase):
    def test_a_multi_line_assistant_reply_stays_one_assistant_message(self):
        rows = [(1, "admin", "make a folder"), (2, "kazi", ASSISTANT_REPLY)]

        messages = build_history_messages(rows)

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "make a folder"},
                {"role": "assistant", "content": ASSISTANT_REPLY},
            ],
        )

    def test_the_current_message_is_left_out(self):
        rows = [(1, "admin", "first"), (2, "kazi", "reply"), (3, "admin", "second")]

        messages = build_history_messages(rows, exclude_message_id=3)

        self.assertEqual([m["content"] for m in messages], ["first", "reply"])

    def test_history_starts_with_a_user_message(self):
        rows = [(1, "kazi", "welcome"), (2, "admin", "hi"), (3, "kazi", "hello")]

        messages = build_history_messages(rows)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])

    def test_neighbours_with_the_same_role_are_merged(self):
        rows = [(1, "admin", "one"), (2, "admin", "two"), (3, "kazi", "a"), (4, "kazi", "b")]

        messages = build_history_messages(rows)

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "one\n\ntwo"},
                {"role": "assistant", "content": "a\n\nb"},
            ],
        )

    def test_the_wake_word_is_stripped_from_user_messages(self):
        messages = build_history_messages([(1, "admin", "@kazi  list the files")])

        self.assertEqual(messages, [{"role": "user", "content": "list the files"}])

    def test_speakers_are_named_only_in_a_room_with_several_people(self):
        rows = [(1, "amina", "@kazi what is the plan?"), (2, "kazi", "Here it is."), (3, "jon", "thanks")]

        messages = build_history_messages(rows, multi_user=True)

        self.assertEqual(messages[0], {"role": "user", "content": "amina: what is the plan?"})
        self.assertEqual(messages[1], {"role": "assistant", "content": "Here it is."})
        self.assertEqual(messages[2], {"role": "user", "content": "jon: thanks"})

    def test_empty_messages_are_skipped(self):
        messages = build_history_messages([(1, "admin", "  "), (2, "admin", "@kazi"), (3, "admin", "real")])

        self.assertEqual(messages, [{"role": "user", "content": "real"}])

    def test_history_over_the_budget_drops_the_oldest_and_still_starts_with_a_user(self):
        rows = [
            (1, "admin", "a" * 50),
            (2, "kazi", "b" * 50),
            (3, "admin", "c" * 50),
            (4, "kazi", "d" * 50),
        ]

        messages = build_history_messages(rows, max_chars=120)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "c" * 50)

    def test_one_oversized_message_is_kept_rather_than_sending_nothing(self):
        messages = build_history_messages([(1, "admin", "x" * 500)], max_chars=100)

        self.assertEqual(len(messages), 1)

    def test_an_oversized_message_does_not_cost_the_reply_that_followed_it(self):
        paste = "START " + "x" * 5000 + " END"
        rows = [(1, "admin", "hello"), (2, "kazi", "hi"), (3, "admin", paste), (4, "kazi", "Line 3 is the bug.")]

        messages = build_history_messages(rows, max_chars=1000)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "Line 3 is the bug.")
        self.assertTrue(messages[0]["content"].startswith("START "))
        self.assertTrue(messages[0]["content"].endswith(" END"))
        self.assertIn("trimmed for length", messages[0]["content"])
        self.assertLessEqual(sum(len(m["content"]) for m in messages), 1000)

    def test_an_oversized_reply_is_shortened_and_the_question_before_it_stays_whole(self):
        rows = [(1, "admin", "show me the log"), (2, "kazi", "y" * 5000)]

        messages = build_history_messages(rows, max_chars=1000)

        self.assertEqual(messages[0], {"role": "user", "content": "show me the log"})
        self.assertLessEqual(sum(len(m["content"]) for m in messages), 1000)

    def test_history_inside_the_budget_is_not_touched(self):
        rows = [(1, "admin", "a" * 400), (2, "kazi", "b" * 400)]

        messages = build_history_messages(rows, max_chars=800)

        self.assertEqual([m["content"] for m in messages], ["a" * 400, "b" * 400])


class WakeWordTests(SimpleTestCase):
    def test_addressed_messages_lose_the_wake_word(self):
        self.assertEqual(strip_wake_word("@kazi list the files"), "list the files")
        self.assertEqual(strip_wake_word("@Kazi, yes"), "yes")
        self.assertEqual(strip_wake_word("@kazi: run it"), "run it")
        self.assertEqual(strip_wake_word("@kazi"), "")

    def test_other_words_that_start_the_same_are_not_the_wake_word(self):
        self.assertIsNone(strip_wake_word("@kazimir are you joining?"))
        self.assertIsNone(strip_wake_word("@Kazi's last answer was wrong"))
        self.assertIsNone(strip_wake_word("ask @kazi about it"))
        self.assertIsNone(strip_wake_word("hello"))


class SpeakerTests(SimpleTestCase):
    def test_several_speakers_are_detected_from_who_wrote_the_rows(self):
        self.assertTrue(has_several_speakers([(1, "amina", "a"), (2, "kazi", "b"), (3, "jon", "c")]))
        self.assertFalse(has_several_speakers([(1, "amina", "a"), (2, "kazi", "b"), (3, "Amina", "c")]))


def _reply(by, tools=None, harness=""):
    return {"tools": tools or [], "by": by, "harness": harness}


HARNESS = "I'd like to run command with the following details: ls. Should I go ahead? (yes / no)"


class ToolRecordReplayTests(SimpleTestCase):
    def test_a_reply_with_records_replays_native_tool_blocks(self):
        rows = [
            (1, "admin", "run the two commands"),
            (2, "kazi", "Here is what happened.", _reply(1, tools=[
                {"name": "run_command", "input": {"command": "false"},
                 "status": "error", "shown": '{"status": "error", "message": "boom"}'},
                {"name": "run_command", "input": {"command": "true"},
                 "status": "success", "shown": '{"status": "success"}'},
            ])),
        ]

        messages = build_history_messages(rows, viewer_user_id=1)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant"])
        uses = messages[1]["content"]
        self.assertEqual([b["type"] for b in uses], ["tool_use", "tool_use"])
        self.assertEqual([b["name"] for b in uses], ["run_command", "run_command"])
        self.assertEqual([b["input"] for b in uses], [{"command": "false"}, {"command": "true"}])
        results = messages[2]["content"]
        self.assertEqual([b["type"] for b in results], ["tool_result", "tool_result"])
        self.assertEqual(results[0]["content"], '{"status": "error", "message": "boom"}')
        self.assertEqual(results[1]["content"], '{"status": "success"}')
        self.assertEqual(results[0]["tool_use_id"], uses[0]["id"])
        self.assertEqual(results[1]["tool_use_id"], uses[1]["id"])
        self.assertEqual(messages[3], {"role": "assistant", "content": "Here is what happened."})

    def test_the_model_text_after_the_records_is_the_reply_without_the_harness(self):
        rows = [
            (1, "admin", "run it"),
            (2, "kazi", "I ran it. " + HARNESS, _reply(1, tools=[
                {"name": "run_command", "input": {"command": "ls"},
                 "status": "success", "shown": "listed"},
            ], harness=HARNESS)),
        ]

        messages = build_history_messages(rows, viewer_user_id=1)

        self.assertIn({"role": "assistant", "content": "I ran it."}, messages)
        self.assertFalse(any(
            isinstance(m["content"], str) and HARNESS in m["content"] and m["role"] == "assistant"
            for m in messages
        ))

    def test_harness_text_becomes_a_note_on_the_next_user_message(self):
        rows = [
            (2, "kazi", HARNESS, _reply(1, harness=HARNESS)),
            (3, "admin", "yes"),
        ]

        messages = build_history_messages(rows, viewer_user_id=1)

        self.assertEqual([m["role"] for m in messages], ["user"])
        self.assertEqual(messages[0]["content"], f"[Harness: {HARNESS}]\nyes")

    def test_another_users_records_are_not_replayed(self):
        rows = [
            (1, "bob", "run it"),
            (2, "kazi", "Done.", _reply(2, tools=[
                {"name": "run_command", "input": {"command": "ls"},
                 "status": "success", "shown": "listed"},
            ])),
            (3, "admin", "what next?"),
        ]

        messages = build_history_messages(rows, viewer_user_id=1)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[1], {"role": "assistant", "content": "Done."})
        self.assertEqual(_tool_ids(messages), (set(), set()))

    def test_trimming_drops_a_tool_exchange_whole(self):
        rows = [
            (1, "admin", "go"),
            (2, "kazi", "done", _reply(1, tools=[
                {"name": "run_command", "input": {"command": "ls"},
                 "status": "success", "shown": "x" * 400},
            ])),
            (3, "admin", "next"),
            (4, "kazi", "fine"),
        ]

        messages = build_history_messages(rows, max_chars=50, viewer_user_id=1)

        uses, results = _tool_ids(messages)
        self.assertEqual(uses, results)
        self.assertEqual([m["content"] for m in messages], ["next", "fine"])

    def test_an_old_reply_without_records_builds_as_today(self):
        rows = [(1, "admin", "hi"), (2, "kazi", "hello")]

        messages = build_history_messages(rows, viewer_user_id=1)

        self.assertEqual(
            messages,
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        )
