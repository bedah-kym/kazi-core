/**
 * Mathia Onboarding Tour
 * Value-driven, user-centric tour for non-tech users.
 * Expanded with Wallet, Dashboard, Settings, Reminders, and Create Room.
 */

document.addEventListener('DOMContentLoaded', () => {
    const driver = window.driver.js.driver;
    const mentionHint = window.isAiOnlyRoom
        ? 'Just type like you\'re talking to a friend. Ask for advice, a summary, or a joke!'
        : 'Start with @mathia then just type like you\'re talking to a friend, e.g. @mathia how is the weather in Nairobi.';

    const driverObj = driver({
        showProgress: true,
        animate: true,
        allowClose: true,
        overlayColor: 'rgba(0, 0, 0, 0.75)',
        nextBtnText: 'Next →',
        prevBtnText: '← Back',
        doneBtnText: 'Ready to Rock!',
        steps: [
            {
                popover: {
                    title: 'Welcome to Mathia! 👋',
                    description: 'We\'re thrilled to have you here. Mathia is designed to be your "Smarter Sidekick"—helping you stay organized without the technical headaches.',
                    side: "center",
                    align: 'start'
                }
            },
            {
                element: '.people-list',
                popover: {
                    title: 'Your Command Center 🏠',
                    description: 'This is where all your conversations live. Switch between different "Rooms" to keep your projects and chats neatly separated.',
                    side: "right",
                    align: 'start'
                }
            },
            {
                element: 'button[data-bs-target="#createRoomModal"]',
                popover: {
                    title: 'Start a New Space ➕',
                    description: 'Need a new area for a project or specialized chat? Just click "+" to create a fresh room in seconds.',
                    side: "right",
                    align: 'center'
                }
            },
            {
                element: 'a[title="Dashboard"]',
                popover: {
                    title: 'The Big Picture 📊',
                    description: 'Jump to your Dashboard anytime for a high-level view of your activity and what needs your attention next.',
                    side: "bottom",
                    align: 'center'
                }
            },
            {
                element: 'a[title="Wallet"]',
                popover: {
                    title: 'Your Digital Wallet 💳',
                    description: 'Manage your balance and transactions securely. Quick access to your funds, whenever you need them.',
                    side: "bottom",
                    align: 'center'
                }
            },
            {
                element: 'a[title="Reminders"]',
                popover: {
                    title: 'Never Miss a Beat 🔔',
                    description: 'Set and view your personal reminders. Mathia makes sure you\'re always on top of your schedule.',
                    side: "bottom",
                    align: 'center'
                }
            },
            {
                element: 'a[title="Settings"]',
                popover: {
                    title: 'Make it Yours ⚙️',
                    description: 'Adjust your preferences and profile to tailor the Mathia experience exactly how you like it.',
                    side: "bottom",
                    align: 'center'
                }
            },
            {
                element: '#contextPanelToggle',
                popover: {
                    title: 'The "Vault" of Knowledge 🧠',
                    description: 'Mathia remembers important details so you don\'t have to. Click this brain icon to see what your assistant has saved for you.',
                    side: "left",
                    align: 'start'
                }
            },
            {
                element: '.btn-calendar',
                popover: {
                    title: 'Scheduling Magic ✨',
                    description: 'Click the calendar to sync your schedule. Say goodbye to messy booking emails—Mathia handles it for you.',
                    side: "right",
                    align: 'end'
                }
            },
            {
                element: '#chat-message-input',
                popover: {
                    title: 'Just Say Hi! 💬',
                    description: mentionHint,
                    side: "top",
                    align: 'center'
                }
            },
            {
                popover: {
                    title: 'You\'re All Set! 🎉',
                    description: 'You\'ve got the keys to the castle. Feel free to explore, and remember—Mathia is always here to help. Have fun!',
                    side: "center",
                    align: 'center'
                }
            }
        ]
    });

    // Handle "Take Tour" button click
    const startTourBtn = document.getElementById('startTour');
    if (startTourBtn) {
        startTourBtn.addEventListener('click', (e) => {
            e.preventDefault();
            driverObj.drive();
        });
    }

    // Auto-start for first-time users
    if (!localStorage.getItem('mathia_tour_seen')) {
        setTimeout(() => {
            driverObj.drive();
            localStorage.setItem('mathia_tour_seen', 'true');
        }, 1500);
    }
});
