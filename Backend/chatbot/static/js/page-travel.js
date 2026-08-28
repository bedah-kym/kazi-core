/* Travel planner wizard. Functions are global because the template
   wires them via inline onclick handlers. */
(function () {
    'use strict';

    var interests = [];

    function setStep(step) {
        document.querySelectorAll('.wizard-step').forEach(function (el) {
            el.classList.remove('active');
        });
        var target = document.getElementById('step-' + step);
        if (target) target.classList.add('active');

        document.querySelectorAll('.travel-step').forEach(function (el, index) {
            el.classList.toggle('active', index + 1 === step);
        });
    }

    function nextStep(step) {
        if (step === 2) {
            var destination = document.getElementById('destination').value.trim();
            var startDate = document.getElementById('start_date').value;
            var endDate = document.getElementById('end_date').value;
            if (!destination || !startDate || !endDate) {
                window.alert('Please fill in destination and dates.');
                return;
            }
        }
        if (step === 3) {
            updateSummary();
        }
        setStep(step);
    }

    function prevStep(step) {
        setStep(step);
    }

    function toggleInterest(card) {
        var value = card.getAttribute('data-value');
        card.classList.toggle('selected');
        var idx = interests.indexOf(value);
        if (idx >= 0) {
            interests.splice(idx, 1);
        } else {
            interests.push(value);
        }
        document.getElementById('interestsInput').value = interests.join(',');
    }

    function updateSummary() {
        var destination = document.getElementById('destination').value.trim();
        var startDate = document.getElementById('start_date').value;
        var endDate = document.getElementById('end_date').value;
        var budget = document.getElementById('budget').value;
        var budgetLabel = budget === 'low' ? 'Budget' : budget === 'high' ? 'Premium' : 'Standard';

        document.getElementById('summaryDestination').textContent = destination || '-';
        document.getElementById('summaryDates').textContent = startDate && endDate ? (startDate + ' to ' + endDate) : '-';
        document.getElementById('summaryBudget').textContent = budgetLabel;
        document.getElementById('summaryInterests').textContent = interests.length ? interests.join(', ') : 'Not set';
    }

    window.nextStep = nextStep;
    window.prevStep = prevStep;
    window.toggleInterest = toggleInterest;
})();
