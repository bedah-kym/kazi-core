"""Persona and skill management pages for the user dashboard.

Users customize *their* personas here (name, description, scope, ceiling,
approval boundary, avatar, assigned skills) and manage skills
(create staged / promote / pin). Persona creation stays admin-side; users
propose new personas through `@admin` in chat (workflows PersonaRequest).
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from chatbot.models import Chatroom
from orchestration.personas import persona_bounds
from orchestration.skill_registry import (
    discover_skills,
    set_skill_pinned,
    transition_skill,
)
from workflows.models import Persona, PersonaRequest
from workflows.promotion import CONTRACT_SECTIONS, write_staged_skill

logger = logging.getLogger(__name__)


def _my_personas(user):
    return Persona.objects.filter(user=user).order_by('name')


def _my_rooms(user):
    return Chatroom.objects.filter(participants__User=user).distinct().order_by('-id')


@login_required
def personas(request):
    if request.method == 'POST':
        persona = get_object_or_404(
            Persona, id=request.POST.get('persona_id'), user=request.user,
        )
        if request.POST.get('action') == 'bind_room':
            room_id = (request.POST.get('room_id') or '').strip()
            if room_id:
                room = get_object_or_404(
                    Chatroom, id=room_id, participants__User=request.user,
                )
                # One persona per room: unbind any other persona from it.
                Persona.objects.filter(room=room).exclude(id=persona.id).update(room=None)
                persona.room = room
            else:
                persona.room = None
            persona.save(update_fields=['room', 'updated_at'])
            messages.success(request, f"Room binding updated for '{persona.name}'.")
        return redirect('users:personas')

    return render(request, 'users/personas.html', {
        'personas': _my_personas(request.user),
        'rooms': _my_rooms(request.user),
        'requests': PersonaRequest.objects.filter(user=request.user).order_by('-created_at')[:10],
    })


@login_required
def persona_edit(request, persona_id):
    persona = get_object_or_404(Persona, id=persona_id, user=request.user)

    if request.method == 'POST':
        persona.name = (request.POST.get('name') or persona.name).strip()[:100] or persona.name
        persona.description = request.POST.get('description') or ''
        persona.tool_scope = [
            item.strip().lower()
            for item in (request.POST.get('tool_scope') or '').split(',')
            if item.strip()
        ]
        ceiling = request.POST.get('risk_ceiling')
        if ceiling in ('low', 'medium', 'high'):
            persona.risk_ceiling = ceiling
        persona.approval_boundary = [
            item.strip().lower()
            for item in (request.POST.get('approval_boundary') or '').split(',')
            if item.strip()
        ]
        persona.skills = request.POST.getlist('skills')
        if request.FILES.get('avatar'):
            persona.avatar = request.FILES['avatar']
        try:
            persona.full_clean(exclude=['user', 'created_from', 'room'])
        except Exception as exc:
            messages.error(request, f"Could not save: {exc}")
            return redirect('users:persona_edit', persona_id=persona.id)
        persona.save()
        messages.success(request, "Persona updated.")
        return redirect('users:personas')

    return render(request, 'users/persona_edit.html', {
        'persona': persona,
        'bounds': persona_bounds(persona),
        'active_skills': [skill['name'] for skill in discover_skills() if skill['stage'] == 'active'],
    })


@login_required
def skills(request):
    # The skill library is shared across every install user: promotion,
    # pinning and creation are staff-only. Per-user customization lives in
    # persona.skills on the persona edit page (and chat's staged save_skill).
    if not request.user.is_staff:
        raise PermissionDenied("Skill management is staff-only.")

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            name = (request.POST.get('name') or '').strip()
            if not name:
                messages.error(request, "Skill name is required.")
            else:
                contract = {
                    section: (request.POST.get(section) or '').strip()
                    for section in CONTRACT_SECTIONS
                }
                description = (request.POST.get('description') or '').strip()
                tools = [
                    item.strip()
                    for item in (request.POST.get('tools') or '').split(',')
                    if item.strip()
                ]
                try:
                    path = write_staged_skill(name, description, tools, contract)
                    messages.success(request, f"Staged skill created: {path}")
                except Exception as exc:
                    messages.error(request, f"Could not create skill: {exc}")
        else:
            skill_name = (request.POST.get('skill') or '').strip()
            if action == 'transition':
                result = transition_skill(skill_name, request.POST.get('target') or '')
                text = result.get('message') or f"{skill_name} -> {request.POST.get('target')}"
                (messages.success if result.get('status') == 'success' else messages.error)(request, text)
            elif action == 'pin':
                result = set_skill_pinned(skill_name, request.POST.get('pinned') == '1')
                text = result.get('message') or f"Pin updated for {skill_name}."
                (messages.success if result.get('status') == 'success' else messages.error)(request, text)
        return redirect('users:skills')

    return render(request, 'users/skills.html', {
        'skills': discover_skills(include_inactive=True),
    })
