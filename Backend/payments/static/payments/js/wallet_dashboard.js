(() => {
  const getCookie = (name) => {
    const cookieValue = document.cookie
      .split(";")
      .map((cookie) => cookie.trim())
      .find((cookie) => cookie.startsWith(`${name}=`));
    return cookieValue ? decodeURIComponent(cookieValue.split("=")[1]) : "";
  };

  const initDepositForm = () => {
    const form = document.getElementById("depositForm");
    if (!form) {
      return;
    }

    const btn = document.getElementById("btnDeposit");
    const depositUrl = form.dataset.depositUrl;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!depositUrl) {
        alert("Deposit endpoint unavailable.");
        return;
      }

      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i> Processing...';

      const formData = new FormData(form);

      try {
        const response = await fetch(depositUrl, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCookie("csrftoken"),
          },
          credentials: "same-origin",
          body: formData,
        });
        const data = await response.json();
        if (response.ok) {
          alert("Success! Check your phone.");
          window.location.reload();
        } else {
          alert(`Error: ${data.error || "Failed"}`);
        }
      } catch (err) {
        console.error(err);
        alert("Connection error");
      } finally {
        btn.disabled = false;
        btn.innerHTML = "Initiate Payment";
      }
    });
  };

  const initChart = () => {
    const canvas = document.getElementById("transactionChart");
    if (!canvas || typeof window.Chart === "undefined") {
      return;
    }

    let labels = [];
    let values = [];
    try {
      labels = JSON.parse(canvas.dataset.labels || "[]");
      values = JSON.parse(canvas.dataset.values || "[]");
    } catch (err) {
      console.error("Failed to parse chart data", err);
      return;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    new window.Chart(context, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Volume",
            data: values,
            backgroundColor: "#18181b",
            borderRadius: 4,
            barThickness: 20,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: {
            grid: { color: "#e4e4e7", borderDash: [4, 4] },
            border: { display: false },
          },
        },
      },
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    initDepositForm();
    initChart();
  });
})();
