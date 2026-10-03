from django.db import migrations


def rename_bot_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    kazi = User.objects.filter(username='kazi').first()
    mathia = User.objects.filter(username='mathia').first()
    if mathia is None:
        return
    if kazi is not None and kazi.id != mathia.id:
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
