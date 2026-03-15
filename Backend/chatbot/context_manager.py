"""
Context Manager for Mathia AI
Handles storage, retrieval, and LLM-ready formatting of conversation context.
Supports Cross-Room context sharing for high-priority notes.
"""
import logging
import json
import math
from datetime import datetime, date, timedelta
from django.utils import timezone
from .models import RoomContext, RoomNote, AIConversation, DocumentUpload

logger = logging.getLogger(__name__)

class ContextManager:
    """
    Manages the 'Memory' of the AI.
    - Retrieves relevant notes/summary for prompt injection.
    - Handles Cross-Room sharing (e.g. User's generic constraints apply to all rooms).
    """

    @staticmethod
    def get_context_for_ai(chatroom, lookback_hours=24):
        """
        Get structured context for the AI System Prompt.
        Includes:
        1. Room Summary & Active Topics
        2. Recent Notes (Decisions, Action Items)
        3. Cross-Room Logic (High Priority notes from other rooms for this user/workspace)
        4. Layered Memory (facts, preferences, episodes)
        """
        try:
            # 1. Get Local Room Context
            context_obj, created = RoomContext.objects.get_or_create(chatroom=chatroom)
            
            # 2. Get Ranked Local Notes
            local_notes = ContextManager._get_ranked_notes(context_obj)
            
            # Format Local Data
            context_data = {
                "summary": context_obj.summary or "",
                "active_topics": context_obj.active_topics or [],
                "recent_notes": [ContextManager._format_note(n) for n in local_notes],
                "latest_daily_summary": "",
                "memory_facts": [],
                "memory_preferences": [],
                "memory_episodes": [],
                "memory_updated_at": None,
            }

            latest_summary = context_obj.daily_summaries.order_by('-date').first()
            if latest_summary:
                context_data["latest_daily_summary"] = latest_summary.summary

            memory_facts = ContextManager._filter_memory_entries(
                context_obj.memory_facts,
                max_items=6,
                max_age_days=365,
                min_confidence=0.45,
            )
            memory_preferences = ContextManager._filter_memory_entries(
                context_obj.memory_preferences,
                max_items=6,
                max_age_days=365,
                min_confidence=0.35,
            )
            memory_episodes = ContextManager._filter_memory_entries(
                context_obj.memory_episodes,
                max_items=4,
                max_age_days=365,
                min_confidence=None,
            )
            context_data.update({
                "memory_facts": memory_facts,
                "memory_preferences": memory_preferences,
                "memory_episodes": memory_episodes,
                "memory_updated_at": (
                    context_obj.memory_updated_at.isoformat()
                    if context_obj.memory_updated_at else None
                ),
            })
            
            # 3. GLOBAL/CROSS-ROOM CONTEXT (The "Memory" across rooms)
            # Find high-priority or 'insight' notes from other rooms involving these participants
            # For simplicity in this MVP: We fetch notes created by the room creator in *any* room
            # that are marked 'high' priority or 'insight' type.
            
            # Determine "Key User" (usually room creator or owner)
            key_user = None
            """
            if chatroom.name.startswith('private_'):
                # Private room logic (if applicable)
                pass """
            
            # Use the first admin or just standard cross-room fetch based on workspace if we had it.
            # Here we'll search notes created by participants of this room in OTHER rooms.
            participants = chatroom.participants.all()
            users = [m.User for m in participants]
            
            global_notes = RoomNote.objects.filter(
                created_by__in=users,
                priority='high'
            ).exclude(room_context=context_obj).order_by('-created_at')[:3]
            
            if global_notes:
                context_data['global_notes'] = [ContextManager._format_note(n) for n in global_notes]
            
            # 4. Get Processed Documents Context
            # Fetch the 3 most recent successfully processed documents for this room
            docs = DocumentUpload.objects.filter(
                chatroom=chatroom,
                status='completed'
            ).order_by('-uploaded_at')[:3]
            
            if docs:
                context_data['documents'] = [{
                    "id": d.id,
                    "type": d.file_type,
                    "text": d.processed_text[:2000], # Limit per doc
                    "metadata": d.extracted_metadata
                } for d in docs]
                
            return context_data
            
        except Exception as e:
            logger.error(f"Error getting context: {e}")
            return {"summary": "", "notes": []}

    @staticmethod
    def get_context_prompt(room_id):
        """
        Returns a formatted string to append to the System Prompt.
        """
        from .models import Chatroom
        try:
            room = Chatroom.objects.get(id=room_id)
            data = ContextManager.get_context_for_ai(room)
            
            prompt_parts = []
            
            # Add Summary
            if data['summary']:
                prompt_parts.append(f"ROOM CONTEXT:\n{data['summary']}")
            
            # Add Active Topics
            if data['active_topics']:
                prompt_parts.append(f"TOPICS: {', '.join(data['active_topics'])}")

            # Add Memory (facts, preferences, episodes)
            fact_lines = []
            for item in data.get("memory_facts") or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                value = str(item.get("value") or "").strip()
                if not key or not value:
                    continue
                line = f"- {key}: {value}"
                confidence = item.get("confidence")
                if confidence is not None:
                    try:
                        line = f"{line} (confidence {float(confidence):.2f})"
                    except (TypeError, ValueError):
                        pass
                fact_lines.append(line)
            if fact_lines:
                prompt_parts.append("KNOWN FACTS:")
                prompt_parts.extend(fact_lines)

            pref_lines = []
            for item in data.get("memory_preferences") or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                value = str(item.get("value") or "").strip()
                if not key or not value:
                    continue
                pref_lines.append(f"- {key}: {value}")
            if pref_lines:
                prompt_parts.append("PREFERENCES:")
                prompt_parts.extend(pref_lines)

            episode_lines = []
            for item in data.get("memory_episodes") or []:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "").strip()
                if not summary:
                    continue
                details = []
                date_value = str(item.get("date") or "").strip()
                if date_value:
                    details.append(date_value)
                importance = str(item.get("importance") or "").strip()
                if importance:
                    details.append(f"importance {importance}")
                if details:
                    episode_lines.append(f"- {summary} ({', '.join(details)})")
                else:
                    episode_lines.append(f"- {summary}")
            if episode_lines:
                prompt_parts.append("EPISODIC MEMORY:")
                prompt_parts.extend(episode_lines)
            
            # Add Local Notes
            if data['recent_notes']:
                prompt_parts.append("IMPORTANT NOTES:")
                for note in data['recent_notes']:
                    status = note.get('status', '')
                    prompt_parts.append(
                        f"- [#{note['id']}] [{note['type'].upper()}]{status} {note['content']}"
                    )

            if data.get('latest_daily_summary'):
                prompt_parts.append(f"DAILY SUMMARY:\n{data['latest_daily_summary']}")
            
            # Add Global/Cross-Room Notes
            if data.get('global_notes'):
                prompt_parts.append("RELEVANT MEMORY (From other chats):")
                for note in data['global_notes']:
                    prompt_parts.append(f"- [MEMORY] {note['content']}")
            
            # Add Document Context
            if data.get('documents'):
                prompt_parts.append("REFERENCED DOCUMENTS:")
                for doc in data['documents']:
                    prompt_parts.append(f"--- DOCUMENT ID {doc['id']} ({doc['type']}) ---")
                    prompt_parts.append(doc['text'])
                    prompt_parts.append("--- END DOCUMENT ---")
                    
            if not prompt_parts:
                return ""
                
            return "\n\n" + "\n".join(prompt_parts)
            
        except Exception as e:
            logger.error(f"Error building context prompt: {e}")
            return ""

    @staticmethod
    def add_note(chatroom, note_type, content, created_by, tags=None, priority='medium'):
        """
        Manually add a note to the context
        """
        context, _ = RoomContext.objects.get_or_create(chatroom=chatroom)
        
        note = RoomNote.objects.create(
            room_context=context,
            note_type=note_type,
            content=content,
            created_by=created_by,
            tags=tags or [],
            priority=priority
        )
        return note

    @staticmethod
    def _parse_entry_datetime(value):
        if not value:
            return None
        if isinstance(value, datetime):
            dt_value = value
        elif isinstance(value, date):
            dt_value = datetime.combine(value, datetime.min.time())
        elif isinstance(value, str):
            try:
                dt_value = datetime.fromisoformat(value)
            except ValueError:
                try:
                    dt_value = datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return None
        else:
            return None
        if timezone.is_naive(dt_value):
            dt_value = timezone.make_aware(dt_value)
        return dt_value

    @staticmethod
    def _entry_timestamp(entry):
        if not isinstance(entry, dict):
            return None
        for field in ("updated_at", "date", "created_at"):
            dt_value = ContextManager._parse_entry_datetime(entry.get(field))
            if dt_value:
                return dt_value
        return None

    @staticmethod
    def _memory_sort_key(entry):
        entry_ts = ContextManager._entry_timestamp(entry)
        ts_value = entry_ts.timestamp() if entry_ts else 0
        importance_weight = 0
        confidence = 0.0
        if isinstance(entry, dict):
            importance = str(entry.get("importance") or "").lower()
            importance_weight = {"high": 2, "medium": 1, "low": 0}.get(importance, 0)
            confidence_value = entry.get("confidence")
            try:
                confidence = float(confidence_value)
            except (TypeError, ValueError):
                confidence = 0.0
        return (importance_weight, confidence, ts_value)

    @staticmethod
    def _filter_memory_entries(entries, max_items=6, max_age_days=365, min_confidence=None):
        if not entries:
            return []
        cutoff = None
        if max_age_days:
            cutoff = timezone.now() - timedelta(days=max_age_days)
        filtered = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if min_confidence is not None and entry.get("confidence") is not None:
                try:
                    if float(entry.get("confidence")) < float(min_confidence):
                        continue
                except (TypeError, ValueError):
                    pass
            entry_ts = ContextManager._entry_timestamp(entry)
            if cutoff and entry_ts and entry_ts < cutoff:
                continue
            filtered.append(entry)
        filtered.sort(key=ContextManager._memory_sort_key, reverse=True)
        return filtered[:max_items]

    @staticmethod
    def _get_ranked_notes(context_obj, limit=8):
        """Fetch up to 30 non-archived notes, score them, return top `limit`."""
        candidates = list(
            RoomNote.objects.filter(
                room_context=context_obj,
                is_archived=False,
            ).order_by('-created_at')[:30]
        )
        if not candidates:
            return []

        now = timezone.now()
        priority_scores = {"high": 1.0, "medium": 0.6, "low": 0.3}
        type_boosts = {
            "decision": 0.3,
            "insight": 0.25,
            "action_item": 0.15,
            "reminder": 0.1,
            "reference": 0.1,
            "written": 0.1,
        }

        scored = []
        for note in candidates:
            age_days = max((now - note.created_at).total_seconds() / 86400, 0)
            touch_ref = note.last_accessed_at or note.updated_at or note.created_at
            untouched_days = max((now - touch_ref).total_seconds() / 86400, 0)

            priority_val = priority_scores.get(note.priority, 0.3)
            recency_val = math.exp(-0.099 * age_days)
            type_boost = type_boosts.get(note.note_type, 0.1)

            # Staleness penalty
            staleness = 0.0
            if note.note_type == "action_item" and not note.is_completed:
                if untouched_days > 14:
                    staleness = -0.3
                elif untouched_days > 7:
                    staleness = -0.15

            # Completion factor
            completion = 0.0
            if note.is_completed and note.completed_at:
                completed_age = (now - note.completed_at).total_seconds() / 86400
                if completed_age < 1:
                    completion = 0.2
                else:
                    completion = -0.4

            score = (
                priority_val * 0.30
                + recency_val * 0.35
                + type_boost * 0.20
                + staleness
                + completion * 0.15
            )
            scored.append((score, note))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [note for _, note in scored[:limit]]

    @staticmethod
    def _format_note(note):
        now = timezone.now()
        status = ""
        if note.is_completed:
            status = " (completed)"
        elif note.note_type == "action_item" and not note.is_completed:
            touch_ref = note.last_accessed_at or note.updated_at or note.created_at
            untouched_days = int((now - touch_ref).total_seconds() / 86400)
            if untouched_days > 7:
                status = f" (stale {untouched_days}d)"

        return {
            "id": note.id,
            "type": note.note_type,
            "content": note.content,
            "priority": note.priority,
            "status": status,
            "created_at": note.created_at.isoformat(),
            "tags": note.tags,
        }

    @staticmethod
    def _compute_semantic_similarity(fact_text, request_text):
        """
        Compute simple keyword-based semantic similarity between fact and request.
        Returns score 0.0-1.0 (higher = more similar).

        Algorithm:
        - Split both into words (lowercased, no stopwords)
        - Count overlapping words
        - Normalize by max(fact_words, request_words)
        """
        import re

        # Common stopwords to ignore
        stopwords = {
            'the', 'a', 'an', 'and', 'or', 'is', 'are', 'am', 'was', 'were',
            'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can',
            'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as',
            'it', 'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she',
            'we', 'they', 'my', 'your', 'his', 'her', 'its', 'our', 'their'
        }

        # Extract words
        fact_words = set(
            word.lower() for word in re.findall(r'\b\w+\b', fact_text)
            if word.lower() not in stopwords
        )
        request_words = set(
            word.lower() for word in re.findall(r'\b\w+\b', request_text)
            if word.lower() not in stopwords
        )

        if not fact_words or not request_words:
            return 0.0

        # Compute Jaccard similarity
        overlap = len(fact_words & request_words)
        union = len(fact_words | request_words)

        if union == 0:
            return 0.0

        return overlap / union

    @staticmethod
    def rank_memory_facts(chatroom, current_request, max_results=3):
        """
        Score memory facts by relevance to current request.

        Ranking formula:
        relevance_score = (recency * 0.3) + (confidence * 0.5) + (semantic_match * 0.2)

        Args:
            chatroom: Chatroom object
            current_request: User's request text
            max_results: How many top facts to return (default: 3)

        Returns:
            List of dicts: [
                {
                    "fact": {...original fact dict...},
                    "score": 0.85,
                    "relevance": 0.6,
                    "recency": 0.8,
                    "confidence": 0.9
                },
                ...
            ]
        """
        try:
            context_obj, _ = RoomContext.objects.get_or_create(chatroom=chatroom)

            if not context_obj.memory_facts:
                return []

            now = timezone.now()
            scored_facts = []

            for fact in context_obj.memory_facts:
                if not isinstance(fact, dict):
                    continue

                # 1. SEMANTIC RELEVANCE (0.0-1.0)
                fact_text = str(fact.get("value", ""))
                if not fact_text:
                    continue

                semantic_match = ContextManager._compute_semantic_similarity(
                    fact_text, current_request
                )

                # 2. RECENCY (0.0-1.0, higher = more recent)
                entry_ts = ContextManager._entry_timestamp(fact)
                if entry_ts:
                    age_seconds = (now - entry_ts).total_seconds()
                    age_days = age_seconds / (24 * 3600)
                    # Decay: recent (0 days) = 1.0, old (365 days) = 0.0
                    recency_score = max(0.0, 1.0 - (age_days / 365.0))
                else:
                    recency_score = 0.5  # Default if no timestamp

                # 3. CONFIDENCE (normalize to 0.0-1.0)
                confidence_value = fact.get("confidence")
                try:
                    confidence_score = float(confidence_value)
                    # Clamp to [0, 1]
                    confidence_score = min(max(confidence_score, 0.0), 1.0)
                except (TypeError, ValueError):
                    confidence_score = 0.5  # Default

                # 4. COMPUTE FINAL SCORE
                relevance_score = (
                    recency_score * 0.3 +
                    confidence_score * 0.5 +
                    semantic_match * 0.2
                )

                scored_facts.append({
                    "fact": fact,
                    "score": relevance_score,
                    "relevance": semantic_match,
                    "recency": recency_score,
                    "confidence": confidence_score
                })

            # Sort by relevance score (descending) and return top-K
            scored_facts.sort(key=lambda x: x["score"], reverse=True)
            return scored_facts[:max_results]

        except Exception as e:
            logger.error(f"Error ranking memory facts: {e}")
            return []

    @staticmethod
    def get_ranked_context_for_ai(chatroom, current_request, lookback_hours=24):
        """
        Enhanced version of get_context_for_ai() that uses ranked memory facts.
        Injects top-3 relevant facts based on current request.

        Args:
            chatroom: Chatroom object
            current_request: User's current message/request
            lookback_hours: Hours to look back in history (for other context)

        Returns:
            Context dict with ranked memory_facts instead of filtered ones
        """
        # Get base context (includes all memory entries)
        context_data = ContextManager.get_context_for_ai(chatroom, lookback_hours)

        # Rank and filter memory facts by relevance
        ranked_facts = ContextManager.rank_memory_facts(chatroom, current_request, max_results=3)

        # Replace generic filtered facts with ranked facts
        context_data["memory_facts"] = [item["fact"] for item in ranked_facts]
        context_data["memory_facts_ranked"] = ranked_facts  # Include scoring info

        return context_data
