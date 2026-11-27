"""Move legacy plaintext chat text into the room-encrypted payload.

Voice transcripts used to be written to ``Message.content`` and
``Message.voice_transcript`` as plaintext, and pinned notes copied the message
text into ``RoomNote``. This command rewrites those rows in batches without
printing any message text. Run it with ``--dry-run`` first.
"""
import json

from django.core.management.base import BaseCommand

from chatbot.kazi_messages import encrypt_for_room, room_cipher
from chatbot.models import (
    PINNED_NOTE_LABEL,
    VOICE_MESSAGE_PLACEHOLDER,
    VOICE_STATUS_MARKER,
    Message,
    RoomNote,
)

BATCH_SIZE = 200
LEGACY_PINNED_NOTE_PREFIX = "Pinned message: "
LEGACY_PINNED_NOTE_SUFFIX = "..."
LEGACY_PINNED_NOTE_MAX_TEXT = 100


def _is_encrypted(raw):
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and "data" in parsed and "nonce" in parsed


def _is_legacy_pinned_content(content):
    if not isinstance(content, str):
        return False
    if not content.startswith(LEGACY_PINNED_NOTE_PREFIX):
        return False
    if not content.endswith(LEGACY_PINNED_NOTE_SUFFIX):
        return False
    limit = len(LEGACY_PINNED_NOTE_PREFIX) + LEGACY_PINNED_NOTE_MAX_TEXT + len(
        LEGACY_PINNED_NOTE_SUFFIX
    )
    return len(content) <= limit


def _voice_plaintext(message):
    """Return the legacy plaintext transcript, or "" when the row is already clean."""
    transcript = message.voice_transcript or ""
    if transcript and transcript not in (VOICE_STATUS_MARKER, VOICE_MESSAGE_PLACEHOLDER):
        return transcript
    content = message.content or ""
    if (
        content
        and content not in (VOICE_STATUS_MARKER, VOICE_MESSAGE_PLACEHOLDER)
        and not _is_encrypted(content)
    ):
        return content
    return ""


def _iter_batches(queryset):
    last_pk = 0
    while True:
        batch = list(
            queryset.filter(pk__gt=last_pk).order_by("pk")[:BATCH_SIZE]
        )
        if not batch:
            return
        yield batch
        last_pk = batch[-1].pk


class Command(BaseCommand):
    help = "Move legacy plaintext voice transcripts and pinned note text into encrypted storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        voice_fixed = 0
        note_fixed = 0
        note_kept = 0
        failed = 0

        for batch in _iter_batches(Message.objects.filter(is_voice=True)):
            for message in batch:
                transcript = _voice_plaintext(message)
                if not transcript:
                    continue
                room = message.chatroom_set.first()
                if room is None or room_cipher(room) is None:
                    failed += 1
                    continue
                if dry_run:
                    voice_fixed += 1
                    continue
                encrypted = encrypt_for_room(room, {
                    "content": transcript,
                    "timestamp": str(message.timestamp),
                    "voice": True,
                })
                if encrypted is None:
                    failed += 1
                    continue
                Message.objects.filter(pk=message.pk).update(
                    content=json.dumps(encrypted),
                    voice_transcript=VOICE_STATUS_MARKER,
                )
                voice_fixed += 1

        for batch in _iter_batches(
            RoomNote.objects.exclude(source_message_id__isnull=True)
        ):
            for note in batch:
                if not note.source_message_id:
                    continue
                content = note.content or ""
                legacy = _is_legacy_pinned_content(content)
                if content != PINNED_NOTE_LABEL and not legacy:
                    note_kept += 1
                needs_source_clear = bool(note.source_message_content)
                if not needs_source_clear and not legacy:
                    continue
                if dry_run:
                    note_fixed += 1
                    continue
                updates = {}
                if needs_source_clear:
                    updates["source_message_content"] = ""
                if legacy:
                    updates["content"] = PINNED_NOTE_LABEL
                RoomNote.objects.filter(pk=note.pk).update(**updates)
                note_fixed += 1

        if dry_run:
            self.stdout.write(
                f"Dry run: would fix {voice_fixed} voice message(s) and "
                f"{note_fixed} pinned note(s); {note_kept} edited note(s) kept; "
                f"{failed} row(s) could not be fixed."
            )
        else:
            self.stdout.write(
                f"Fixed {voice_fixed} voice message(s) and {note_fixed} pinned "
                f"note(s); {note_kept} edited note(s) kept; "
                f"{failed} row(s) could not be fixed."
            )
