// Calendar Modal - Bootstrap Version (No conflicts with settings)

(function () {
    console.log('[calendar] Script loaded')
    // Get modal element
    const calendarModal = document.getElementById('calendarModal')
    const connectBtn = document.getElementById('connectCalendly')
    const disconnectBtn = document.getElementById('disconnectCalendly')
    const copyLinkBtn = document.getElementById('copyBookingLink')
    const upcomingMeetings = document.getElementById('upcomingMeetings')
    const connectedUser = document.getElementById('connectedUser')
    const connectedEvent = document.getElementById('connectedEvent')
    const editLink = document.getElementById('editOnCalendly')

    console.log('[calendar] Elements found:', {
        modal: !!calendarModal,
        connect: !!connectBtn,
        disconnect: !!disconnectBtn
    })

    // Initialize Bootstrap modal instance
    let modalInstance = null
    if (calendarModal) {
        modalInstance = new bootstrap.Modal(calendarModal)

        // Fetch status when modal opens
        calendarModal.addEventListener('show.bs.modal', function () {
            console.log('[calendar] Modal opening, fetching status...')
            fetchStatus()
        })
        calendarModal.addEventListener('hide.bs.modal', () => document.activeElement?.blur());

    }

    // Fetch connection status
    async function fetchStatus() {
        try {
            console.log('[calendar] Fetching status...')
            const res = await fetch('/api/calendly/user/status/')

            if (!res.ok) {
                console.error('[calendar] Status fetch failed:', res.status)
                return
            }

            const data = await res.json()
            console.log('[calendar] Status:', data)

            if (data.isConnected) {
                // Show connected state
                document.getElementById('calendarStatus').style.display = 'none'
                document.getElementById('calendarConnected').style.display = 'block'

                if (connectedUser) {
                    connectedUser.textContent = data.calendlyUserUri || 'Unknown'
                }
                if (connectedEvent) {
                    connectedEvent.textContent = data.eventTypeName || 'Default Event'
                }
                if (editLink) {
                    editLink.href = data.bookingLink || 'https://calendly.com'
                }

                loadEvents()
            } else {
                // Show not connected state
                document.getElementById('calendarStatus').style.display = 'block'
                document.getElementById('calendarConnected').style.display = 'none'
            }
        } catch (err) {
            console.error('[calendar] fetchStatus error:', err)
        }
    }

    // Connect to Calendly
    if (connectBtn) {
        connectBtn.addEventListener('click', async function (e) {
            e.preventDefault()
            console.log('[calendar] Connect button clicked')

            // Disable button and show loading
            connectBtn.disabled = true
            const originalHTML = connectBtn.innerHTML
            connectBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Connecting...'

            try {
                const res = await fetch('/api/calendly/connect/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCookie('csrftoken'),
                        'Content-Type': 'application/json'
                    }
                })

                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`)
                }

                const data = await res.json()
                console.log('[calendar] Connect response:', data)

                const authUrl = data.authorization_url || data.authorizationUrl

                if (authUrl) {
                    // Open OAuth popup
                    const width = 600
                    const height = 700
                    const left = (screen.width / 2) - (width / 2)
                    const top = (screen.height / 2) - (height / 2)

                    const popup = window.open(
                        authUrl,
                        'calendlyAuth',
                        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`
                    )

                    if (popup) {
                        console.log('[calendar] OAuth popup opened')

                        // Poll for popup close
                        const checkClosed = setInterval(() => {
                            if (popup.closed) {
                                clearInterval(checkClosed)
                                console.log('[calendar] OAuth popup closed')
                                connectBtn.disabled = false
                                connectBtn.innerHTML = originalHTML
                                fetchStatus()
                            }
                        }, 500)
                    } else {
                        alert('Please allow popups to connect with Calendly')
                        connectBtn.disabled = false
                        connectBtn.innerHTML = originalHTML
                    }
                } else {
                    throw new Error('No authorization URL received')
                }
            } catch (err) {
                console.error('[calendar] Connect error:', err)
                alert('Failed to connect: ' + err.message)
                connectBtn.disabled = false
                connectBtn.innerHTML = originalHTML
            }
        })
    }

    // Disconnect from Calendly
    if (disconnectBtn) {
        disconnectBtn.addEventListener('click', async function (e) {
            e.preventDefault()
            console.log('[calendar] Disconnect clicked')

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

                if (res.ok) {
                    console.log('[calendar] Disconnected successfully')
                    fetchStatus()
                }
            } catch (err) {
                console.error('[calendar] Disconnect error:', err)
            }
        })
    }

    // Copy booking link
    if (copyLinkBtn) {
        copyLinkBtn.addEventListener('click', async function (e) {
            e.preventDefault()
            console.log('[calendar] Copy link clicked')

            try {
                const res = await fetch('/api/calendly/user/status/')
                const data = await res.json()

                if (data.bookingLink) {
                    await navigator.clipboard.writeText(data.bookingLink)

                    // Show success feedback
                    const originalHTML = copyLinkBtn.innerHTML
                    copyLinkBtn.innerHTML = '<i class="fas fa-check me-2"></i>Copied!'
                    copyLinkBtn.classList.remove('btn-outline-primary')
                    copyLinkBtn.classList.add('btn-success')

                    setTimeout(() => {
                        copyLinkBtn.innerHTML = originalHTML
                        copyLinkBtn.classList.remove('btn-success')
                        copyLinkBtn.classList.add('btn-outline-primary')
                    }, 2000)
                } else {
                    alert('No booking link available')
                }
            } catch (err) {
                console.error('[calendar] Copy error:', err)
                alert('Failed to copy link')
            }
        })
    }

    // Load upcoming events
    async function loadEvents() {
        if (!upcomingMeetings) return

        upcomingMeetings.innerHTML = '<div class="text-center text-muted py-3"><div class="spinner-border spinner-border-sm"></div></div>'

        try {
            console.log('[calendar] Loading events...')
            const res = await fetch('/api/calendly/user/events/')
            const data = await res.json()

            console.log('[calendar] Events:', data)

            upcomingMeetings.innerHTML = ''

            if (data.events && data.events.length > 0) {
                data.events.forEach(event => {
                    const card = document.createElement('div')
                    card.className = 'meeting-card'
                    card.innerHTML = `
                        <div class="d-flex justify-content-between align-items-start">
                            <div>
                                <div class="fw-bold">${escapeHtml(event.title)}</div>
                                <div class="small text-muted">
                                    <i class="fas fa-clock me-1"></i>${escapeHtml(event.start)}
                                    ${event.duration ? `• ${event.duration} min` : ''}
                                </div>
                            </div>
                            <i class="fas fa-calendar-check text-primary"></i>
                        </div>
                    `
                    upcomingMeetings.appendChild(card)
                })
            } else {
                upcomingMeetings.innerHTML = `
                    <div class="text-center text-muted py-4">
                        <i class="fas fa-calendar-times fa-2x mb-2"></i>
                        <p class="mb-0">No upcoming meetings</p>
                    </div>
                `
            }
        } catch (err) {
            console.error('[calendar] Load events error:', err)
            upcomingMeetings.innerHTML = `
                <div class="text-center text-danger py-3">
                    <i class="fas fa-exclamation-triangle me-2"></i>
                    Failed to load meetings
                </div>
            `
        }
    }

    // Slash commands in chat
    const chatInput = document.getElementById('chat-message-input')
    if (chatInput) {
        chatInput.addEventListener('keydown', async function (e) {
            {
                if (e.key === 'Enter' && !e.shiftKey) {
                    const value = chatInput.value.trim()

                    if (value.startsWith('@mathia')) {
                        return
                    }
                    // /schedule @username
                    if (value.startsWith('/schedule ')) {
                        e.preventDefault()
                        const parts = value.split(' ')
                        const mention = parts[1]

                        if (mention && mention.startsWith('@')) {
                            const username = mention.slice(1)
                            await scheduleWithUser(username)
                            chatInput.value = ''
                        }
                        return
                    }

                    // /calendly or /availability
                    if (value === '/calendly' || value === '/availability') {
                        e.preventDefault()

                        try {
                            const res = await fetch('/api/calendly/user/status/')
                            const data = await res.json()

                            if (data.isConnected && data.bookingLink) {
                                await navigator.clipboard.writeText(data.bookingLink)
                                alert('Your booking link copied to clipboard!')
                            } else {
                                alert('Connect Calendly first to share your booking link')
                                if (modalInstance) modalInstance.show()
                            }
                        } catch (err) {
                            console.error(err)
                        }

                        chatInput.value = ''
                        return
                    }

                    // /calendly connect
                    if (value === '/calendly connect') {
                        e.preventDefault()
                        if (modalInstance) modalInstance.show()
                        chatInput.value = ''
                        return
                    }
                }
            }
        })
    }

    // Profile schedule button
    const profileScheduleBtn = document.getElementById('profileScheduleBtn')

    if (profileScheduleBtn) {
        profileScheduleBtn.addEventListener('click', async function (e) {
            e.preventDefault()
            const username = window.otherUser || ''
            if (username) {
                await scheduleWithUser(username)
            }
        })

        // Check if other user has Calendly and show/hide button
        updateProfileScheduleButton()
    }

    async function updateProfileScheduleButton() {
        if (!profileScheduleBtn) return

        const username = window.otherUser || ''
        if (!username) {
            // Hide button if no username
            profileScheduleBtn.style.display = 'none'
            return
        }

        try {
            const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(username)}/booking-link/`)

            if (!res.ok) {
                console.warn('[calendar] Failed to fetch booking link:', res.status)
                profileScheduleBtn.style.display = 'none'
                return
            }

            const data = await res.json()

            if (data.isConnected) {
                profileScheduleBtn.style.display = 'inline-block'
            } else {
                profileScheduleBtn.style.display = 'none'
            }
        } catch (err) {
            console.error('[calendar] Update profile button error:', err)
            profileScheduleBtn.style.display = 'none'
        }
    }

    // Schedule with a specific user
    async function scheduleWithUser(username) {
        try {
            const res = await fetch(`/api/calendly/user/username/${encodeURIComponent(username)}/booking-link/`)
            const data = await res.json()

            if (data.isConnected && data.bookingLink) {
                openSchedulingModal(data.bookingLink)
            } else {
                alert(`${username} hasn't connected their Calendly yet`)
            }
        } catch (err) {
            console.error('[calendar] Schedule error:', err)
            alert('Failed to load calendar')
        }
    }

    // Open Calendly inline widget in modal
    function openSchedulingModal(bookingUrl) {
        const schedulingModal = document.getElementById('calendlySchedulingModal')
        const calendlyInline = document.getElementById('calendlyInline')

        if (!schedulingModal || !calendlyInline) {
            console.warn('[calendar] Scheduling modal not found, opening in new tab')
            window.open(bookingUrl, '_blank')
            return
        }

        const modal = new bootstrap.Modal(schedulingModal)

        // Clear previous content
        calendlyInline.innerHTML = ''

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
        } catch (err) {
            console.warn('[calendar] Calendly widget error:', err)
            window.open(bookingUrl, '_blank')
        }
    }

    // Helper: Get CSRF token
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

    // Helper: Escape HTML
    function escapeHtml(text) {
        const div = document.createElement('div')
        div.textContent = text
        return div.innerHTML
    }

    console.log('[calendar] Initialization complete')
})();