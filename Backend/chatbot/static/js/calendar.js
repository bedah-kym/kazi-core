// Lightweight calendar panel interactions and API integration

(async function () {
    console.log('[calendar] calendar.js loaded')
    function $(s) { return document.querySelector(s) }
    // Element references (will be populated after DOM ready)
    let openBtn, closeBtn, modalEl, calendarInline, connectBtn, disconnectBtn, copyLinkBtn, upcoming, connectedUser, connectedEvent, editLink, profileScheduleBtn

    // Defer DOM queries until DOM is ready
    function initCalendar() {
        openBtn = $('#openCalendarPanel')
        closeBtn = $('#closeCalendarPanel')
        // We now use a Bootstrap modal for calendar UI
        modalEl = document.getElementById('calendlyModal')
        calendarInline = document.getElementById('calendlyInline')
        console.log('[calendar] modal element:', !!modalEl, 'inline:', !!calendarInline)
        connectBtn = $('#connectCalendly')
        disconnectBtn = $('#disconnectCalendly')
        copyLinkBtn = $('#copyBookingLink')
        upcoming = $('#upcomingMeetings')
        connectedUser = $('#connectedUser')
        connectedEvent = $('#connectedEvent')
        editLink = $('#editOnCalendly')
        profileScheduleBtn = document.getElementById('profileScheduleBtn')
        console.log('[calendar] elements: openBtn=', !!openBtn, 'closeBtn=', !!closeBtn, 'connectBtn=', !!connectBtn, 'disconnectBtn=', !!disconnectBtn)

        function openCalendarModal() {
            if (!modalEl) return console.warn('[calendar] modal element not found')
            try {
                // clear inline area
                if (calendarInline) calendarInline.innerHTML = ''
                const bsModal = new bootstrap.Modal(modalEl)
                bsModal.show()
                fetchStatus()
            } catch (e) { console.error('[calendar] openCalendarModal error', e) }
        }

        function closeCalendarModal() {
            if (!modalEl) return
            try {
                const modalInstance = bootstrap.Modal.getInstance(modalEl)
                if (modalInstance) modalInstance.hide()
            } catch (e) { console.error('[calendar] closeCalendarModal error', e) }
        }

        // Open calendar modal when user clicks calendar icon
        if (openBtn) {
            openBtn.addEventListener('click', (e) => {
                e.preventDefault()
                e.stopPropagation()
                openCalendarModal()
            })
        } else {
            console.warn('[calendar] openBtn not found')
        }

        // closeBtn no longer used (modal has its own close); listen for modal hide to cleanup inline
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', () => {
                try {
                    if (calendarInline) calendarInline.innerHTML = ''
                } catch (e) { console.warn('[calendar] cleanup inline error', e) }
            })
        }

    // wire other handlers that rely on DOM elements (connect/disconnect/etc)
    wireHandlers()
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCalendar)
    } else {
        initCalendar()
    }

    async function fetchStatus() {
        try {
            console.debug('[calendar] fetching /api/calendly/user/status/')
            const res = await fetch('/api/calendly/user/status/')
            console.debug('[calendar] status response status', res.status)
            const data = await res.json()
            console.debug('[calendar] status response json', data)
            if (data.isConnected) {
                document.getElementById('calendarStatus').style.display = 'none'
                document.getElementById('calendarConnected').style.display = 'block'
                connectedUser.textContent = 'Connected as ' + (data.calendlyUserUri || '')
                connectedEvent.textContent = data.eventTypeName || 'Event'
                editLink.href = data.bookingLink || 'https://calendly.com'
                loadEvents()
            } else {
                document.getElementById('calendarStatus').style.display = 'block'
                document.getElementById('calendarConnected').style.display = 'none'
            }
        } catch (err) {
            console.error('[calendar] fetchStatus error:', err)
        }
    }

    // Connect button - Fixed OAuth flow
    if (connectBtn) {
        connectBtn.addEventListener('click', async (e) => {
            e.preventDefault()
            e.stopPropagation()
            console.log('[calendar] connect clicked')

            // Disable button to prevent double clicks
            connectBtn.disabled = true
            connectBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Connecting...'

            try {
                const res = await fetch('/api/calendly/connect/', {
                    method: 'POST',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': getCookie('csrftoken'),
                        'Content-Type': 'application/json'
                    }
                })

                if (!res.ok) {
                    throw new Error(`HTTP error! status: ${res.status}`)
                }

                const data = await res.json()
                console.debug('[calendar] connect response', data)

                const url = data.authorization_url || data.authorizationUrl || data.authorizationURL

                if (url) {
                    console.log('[calendar] Opening OAuth URL:', url)

                    // Calculate center position for popup
                    const width = 600
                    const height = 700
                    const left = (screen.width / 2) - (width / 2)
                    const top = (screen.height / 2) - (height / 2)

                    // Open popup
                    const popup = window.open(
                        url,
                        'calendlyAuth',
                        `width=${width},height=${height},left=${left},top=${top},toolbar=no,menubar=no,scrollbars=yes,resizable=yes`
                    )

                    if (popup) {
                        // Poll for window close
                        const checkClosed = setInterval(() => {
                            if (popup.closed) {
                                clearInterval(checkClosed)
                                console.log('[calendar] OAuth popup closed, refreshing status')
                                fetchStatus()
                                // Re-enable button
                                connectBtn.disabled = false
                                connectBtn.innerHTML = 'Connect Your Calendly'
                            }
                        }, 500)
                    } else {
                        // Popup blocked
                        alert('Please allow popups to connect with Calendly')
                        connectBtn.disabled = false
                        connectBtn.innerHTML = 'Connect Your Calendly'
                    }
                } else {
                    console.error('[calendar] no authorization_url in response', data)
                    alert('Could not get Calendly authorization URL. Please try again.')
                    connectBtn.disabled = false
                    connectBtn.innerHTML = 'Connect Your Calendly'
                }
            } catch (e) {
                console.error('[calendar] connect error', e)
                alert('Calendly connect failed: ' + e.message)
                connectBtn.disabled = false
                connectBtn.innerHTML = 'Connect Your Calendly'
            }
        })
    }

    // Disconnect button
    if (disconnectBtn) {
        disconnectBtn.addEventListener('click', async (e) => {
            e.preventDefault()
            e.stopPropagation()
            console.log('[calendar] disconnect clicked')

            if (!confirm('Are you sure you want to disconnect Calendly?')) {
                return
            }

            try {
                const res = await fetch('/api/calendly/disconnect/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCookie('csrftoken'),
                        'Content-Type': 'application/json'
                    }
                })
                console.debug('[calendar] disconnect response', res.status)
                if (res.ok) {
                    fetchStatus()
                }
            } catch (e) {
                console.error('[calendar] disconnect error', e)
            }
        })
    }

    // Copy booking link
    if (copyLinkBtn) {
        copyLinkBtn.addEventListener('click', async (e) => {
            e.preventDefault()
            e.stopPropagation()
            console.log('[calendar] copy booking link clicked')

            try {
                const res = await fetch('/api/calendly/user/status/')
                const data = await res.json()
                console.debug('[calendar] copy link status', data)

                if (data.bookingLink) {
                    await navigator.clipboard.writeText(data.bookingLink)

                    // Show success feedback
                    const originalText = copyLinkBtn.innerHTML
                    copyLinkBtn.innerHTML = '<i class="fas fa-check me-2"></i>Copied!'
                    copyLinkBtn.classList.add('btn-success')
                    copyLinkBtn.classList.remove('btn-outline-primary')

                    setTimeout(() => {
                        copyLinkBtn.innerHTML = originalText
                        copyLinkBtn.classList.remove('btn-success')
                        copyLinkBtn.classList.add('btn-outline-primary')
                    }, 2000)
                } else {
                    alert('No booking link available')
                }
            } catch (e) {
                console.error('[calendar] copy booking link error', e)
                alert('Failed to copy link')
            }
        })
    }

    async function loadEvents() {
        if (!upcoming) {
            console.warn('[calendar] upcoming element missing')
            return
        }
        upcoming.innerHTML = '<div class="small text-muted">Loading...</div>'

        try {
            console.debug('[calendar] fetching /api/calendly/user/events/')
            const res = await fetch('/api/calendly/user/events/')
            console.debug('[calendar] events response status', res.status)
            const json = await res.json()
            console.debug('[calendar] events json', json)

            upcoming.innerHTML = ''

            if (json.events && json.events.length) {
                json.events.forEach(ev => {
                    const div = document.createElement('div')
                    div.className = 'card mb-2'
                    div.innerHTML = `
                        <div class="card-body p-2">
                            <div class="fw-bold">${ev.title}</div>
                            <div class="small text-muted">${ev.start} • ${ev.duration || ''}min</div>
                        </div>
                    `
                    upcoming.appendChild(div)
                })
            } else {
                upcoming.innerHTML = '<div class="small text-muted">No upcoming meetings</div>'
            }
        } catch (e) {
            console.error('[calendar] loadEvents error', e)
            upcoming.innerHTML = '<div class="small text-muted">Error loading meetings</div>'
        }
    }

    // Helper to get csrftoken
    function getCookie(name) {
        let cookieValue = null
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';')
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim()
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1))
                    break
                }
            }
        }
        return cookieValue
    }

    // Attach handlers that require DOM elements
    function wireHandlers() {
        // Open calendar modal when user clicks calendar icon
        if (openBtn) {
            openBtn.addEventListener('click', (e) => {
                e.preventDefault()
                e.stopPropagation()
                try {
                    if (modalEl) {
                        const bsModal = new bootstrap.Modal(modalEl)
                        bsModal.show()
                        fetchStatus()
                    }
                } catch (err) { console.error('[calendar] open modal error', err) }
            })
        }

        // Cleanup inline area when modal hidden
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', () => {
                try {
                    if (calendarInline) calendarInline.innerHTML = ''
                } catch (e) { console.warn('[calendar] cleanup inline error', e) }
            })
        }

        // Connect button
        if (connectBtn) {
            connectBtn.addEventListener('click', async (e) => {
                e.preventDefault(); e.stopPropagation();
                console.log('[calendar] connect clicked')
                connectBtn.disabled = true
                connectBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Connecting...'
                try {
                    const res = await fetch('/api/calendly/connect/', {
                        method: 'POST',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'X-CSRFToken': getCookie('csrftoken'),
                            'Content-Type': 'application/json'
                        }
                    })
                    if (!res.ok) throw new Error(`HTTP ${res.status}`)
                    const data = await res.json()
                    const url = data.authorization_url || data.authorizationUrl || data.authorizationURL
                    if (url) {
                        const popup = window.open(url, 'calendlyAuth', 'width=600,height=700')
                        if (!popup) {
                            alert('Please allow popups to connect with Calendly')
                            connectBtn.disabled = false
                            connectBtn.innerHTML = 'Connect Your Calendly'
                        } else {
                            const checkClosed = setInterval(() => {
                                if (popup.closed) {
                                    clearInterval(checkClosed)
                                    fetchStatus()
                                    connectBtn.disabled = false
                                    connectBtn.innerHTML = 'Connect Your Calendly'
                                }
                            }, 500)
                        }
                    } else {
                        alert('Could not get Calendly authorization URL')
                        connectBtn.disabled = false
                        connectBtn.innerHTML = 'Connect Your Calendly'
                    }
                } catch (err) {
                    console.error('[calendar] connect error', err)
                    alert('Failed to start Calendly connection: ' + err.message)
                    connectBtn.disabled = false
                    connectBtn.innerHTML = 'Connect Your Calendly'
                }
            })
        }

        // Disconnect
        if (disconnectBtn) {
            disconnectBtn.addEventListener('click', async (e) => {
                e.preventDefault(); e.stopPropagation();
                if (!confirm('Are you sure you want to disconnect Calendly?')) return
                try {
                    const res = await fetch('/api/calendly/disconnect/', { method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken'), 'Content-Type': 'application/json' } })
                    if (res.ok) fetchStatus()
                } catch (err) { console.error('[calendar] disconnect error', err) }
            })
        }

        // Copy booking link
        if (copyLinkBtn) {
            copyLinkBtn.addEventListener('click', async (e) => {
                e.preventDefault(); e.stopPropagation();
                try {
                    const res = await fetch('/api/calendly/user/status/')
                    const data = await res.json()
                    if (data.bookingLink) {
                        await navigator.clipboard.writeText(data.bookingLink)
                        const originalText = copyLinkBtn.innerHTML
                        copyLinkBtn.innerHTML = '<i class="fas fa-check me-2"></i>Copied!'
                        copyLinkBtn.classList.add('btn-success')
                        copyLinkBtn.classList.remove('btn-outline-primary')
                        setTimeout(() => { copyLinkBtn.innerHTML = originalText; copyLinkBtn.classList.remove('btn-success'); copyLinkBtn.classList.add('btn-outline-primary') }, 2000)
                    } else alert('No booking link available')
                } catch (err) { console.error('[calendar] copy link error', err); alert('Failed to copy link') }
            })
        }

        // Profile schedule button
        if (profileScheduleBtn) {
            profileScheduleBtn.addEventListener('click', async (e) => {
                e.preventDefault(); e.stopPropagation()
                const target = otherUser || ''
                if (!target) return
                try {
                    const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(target)}/booking-link/`)
                    const j = await res.json()
                    if (j.isConnected && j.bookingLink) openEmbeddedScheduler(j.bookingLink)
                    else alert('User has not connected Calendly')
                } catch (err) { console.error(err); alert('Error loading calendar') }
            })
            // update button visibility
            (async function () {
                try {
                    const target = otherUser || ''
                    if (!target) return
                    const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(target)}/booking-link/`)
                    const j = await res.json()
                    if (j.isConnected) profileScheduleBtn.style.display = 'inline-block'
                } catch (err) { console.error('[calendar] updateProfileScheduleButton error', err) }
            })()
        }
    }

    // Slash command handler
    const chatInput = document.getElementById('chat-message-input')
    const profileScheduleBtn = document.getElementById('profileScheduleBtn')

    if (chatInput) {
        chatInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                const v = chatInput.value.trim()

                // /schedule @username
                if (v.startsWith('/schedule ')) {
                    e.preventDefault()
                    const mention = v.split(' ')[1]
                    if (mention && mention.startsWith('@')) {
                        const username = mention.slice(1)
                        try {
                            const r = await fetch(`/api/calendly/user/username/${encodeURIComponent(username)}/booking-link/`)
                            const j = await r.json()
                            if (j.isConnected && j.bookingLink) {
                                openEmbeddedScheduler(j.bookingLink)
                                chatInput.value = '' // Clear input
                            } else {
                                alert('This user has not connected Calendly')
                            }
                        } catch (e) {
                            console.error(e)
                            alert('Error fetching booking link')
                        }
                    }
                    return
                }

                // /calendly or /availability
                if (v === '/calendly' || v === '/availability') {
                    e.preventDefault()
                    try {
                        const res = await fetch('/api/calendly/user/status/')
                        const data = await res.json()
                        if (data.isConnected && data.bookingLink) {
                            // Copy to clipboard and show message
                            await navigator.clipboard.writeText(data.bookingLink)
                            alert('Your booking link copied to clipboard:\n' + data.bookingLink)
                        } else {
                            alert('You have not connected Calendly yet. Click the calendar icon to connect.')
                        }
                    } catch (e) {
                        console.error(e)
                        alert('Error retrieving booking link')
                    }
                    chatInput.value = '' // Clear input
                    return
                }

                // /calendly connect
                if (v === '/calendly connect') {
                    e.preventDefault()
                    showPanel()
                    chatInput.value = '' // Clear input
                    return
                }
            }
        })
    }

    // Open Calendly embedded modal
    function openEmbeddedScheduler(bookingUrl) {
        if (!bookingUrl) return

        const modalEl = document.getElementById('calendlyModal')
        const calendlyInline = document.getElementById('calendlyInline')

        if (!modalEl || !calendlyInline) {
            console.warn('[calendar] Modal elements not found, opening in new tab')
            window.open(bookingUrl, '_blank')
            return
        }

        const modal = new bootstrap.Modal(modalEl)
        calendlyInline.innerHTML = '' // Clear previous content

        try {
            if (typeof Calendly !== 'undefined' && Calendly.initInlineWidget) {
                Calendly.initInlineWidget({
                    url: bookingUrl,
                    parentElement: calendlyInline,
                    prefill: {},
                    utm: {}
                })
                modal.show()
            } else {
                throw new Error('Calendly widget not loaded')
            }
        } catch (e) {
            console.warn('[calendar] Calendly.initInlineWidget failed', e)
            // Fallback to new tab
            window.open(bookingUrl, '_blank')
        }
    }

    // Profile schedule button
    async function updateProfileScheduleButton() {
        if (!profileScheduleBtn) return

        try {
            const target = otherUser || ''
            if (!target) return

            const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(target)}/booking-link/`)
            const j = await res.json()

            if (j.isConnected) {
                profileScheduleBtn.style.display = 'inline-block'
            } else {
                profileScheduleBtn.style.display = 'none'
            }
        } catch (e) {
            console.error('[calendar] updateProfileScheduleButton error', e)
        }
    }

    if (profileScheduleBtn) {
        profileScheduleBtn.addEventListener('click', async (e) => {
            e.preventDefault()
            e.stopPropagation()

            const target = otherUser || ''
            if (!target) return

            try {
                const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(target)}/booking-link/`)
                const j = await res.json()

                if (j.isConnected && j.bookingLink) {
                    openEmbeddedScheduler(j.bookingLink)
                } else {
                    alert('User has not connected Calendly')
                }
            } catch (e) {
                console.error(e)
                alert('Error loading calendar')
            }
        })

        updateProfileScheduleButton()
    }

})();