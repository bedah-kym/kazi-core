from django.db import migrations


def _has_owner(user):
    # The assistant's account has no password and never logs in. An account
    # named 'kazi' that does predates the reservation of that username.
    password = user.password or ''
    return bool(
        (password and not password.startswith('!'))
        or user.last_login
        or user.is_staff
        or user.is_superuser
    )


def rename_bot_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Member = apps.get_model('chatbot', 'Member')
    Chatroom = apps.get_model('chatbot', 'Chatroom')

    kazi = User.objects.filter(username='kazi').first()
    mathia = User.objects.filter(username='mathia').first()
    if mathia is None:
        return
    if kazi is not None and kazi.id != mathia.id:
        if _has_owner(kazi):
            raise RuntimeError(
                "An account named 'kazi' looks like a person: it has a password, a "
                "login history or staff rights. That username is reserved for the "
                "assistant, and merging the old bot into it would add that account "
                "to other users' rooms. Rename the account, then run migrate again. "
                "Nothing was changed."
            )
        # Both identities exist: merge the legacy bot into the existing kazi
        # account. Move room memberships so those rooms stay recognised as AI
        # rooms, then retire the old account. Message history stays attached
        # to its original Member row.
        kazi_member, _ = Member.objects.get_or_create(User=kazi)
        legacy_member = Member.objects.filter(User=mathia).first()
        if legacy_member is not None:
            through = Chatroom.participants.through
            room_ids = list(
                through.objects.filter(member_id=legacy_member.id).values_list('chatroom_id', flat=True)
            )
            for room_id in room_ids:
                through.objects.get_or_create(chatroom_id=room_id, member_id=kazi_member.id)
                through.objects.filter(chatroom_id=room_id, member_id=legacy_member.id).delete()
        mathia.is_active = False
        mathia.username = f'mathia-legacy-{mathia.id}'
        mathia.save(update_fields=['is_active', 'username'])
        return
    mathia.username = 'kazi'
    if mathia.first_name == 'Mathia':
        mathia.first_name = 'Kazi'
    if mathia.email == 'mathia@kwikchat.ai':
        mathia.email = 'kazi@kwikchat.ai'
    mathia.save(update_fields=['username', 'first_name', 'email'])


def revert_bot_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    kazi = User.objects.filter(username='kazi').first()
    if kazi is None or kazi.email != 'kazi@kwikchat.ai':
        return
    kazi.username = 'mathia'
    kazi.first_name = 'Mathia'
    kazi.email = 'mathia@kwikchat.ai'
    kazi.save(update_fields=['username', 'first_name', 'email'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0017_remove_language_alter_timezone'),
    ]

    operations = [
        migrations.RunPython(rename_bot_user, revert_bot_user),
    ]
