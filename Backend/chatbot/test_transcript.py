"""History handed to the model keeps real roles and whole messages."""
from django.test import SimpleTestCase

from chatbot.transcript import build_history_messages

ASSISTANT_REPLY = (
    "I'll create the folder and file.\n"
    "Step 2: write the three lines\n"
    "command: mkdir qa_audit\n"
    "\n"
    "Done."
)


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
