(() => {
  const copyToClipboard = async (text, fallbackInput) => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (err) {
        console.error("Clipboard write failed", err);
      }
    }

    if (fallbackInput) {
      fallbackInput.select();
      const copied = document.execCommand("copy");
      fallbackInput.blur();
      return copied;
    }

    return false;
  };

  const initCopyButtons = () => {
    const buttons = document.querySelectorAll("[data-copy-target]");
    if (!buttons.length) {
      return;
    }

    buttons.forEach((button) => {
      button.addEventListener("click", async () => {
        const targetId = button.dataset.copyTarget;
        const input = document.getElementById(targetId);
        if (!input) {
          return;
        }

        const success = await copyToClipboard(input.value, input);
        if (success) {
          alert("Link copied to clipboard!");
        } else {
          alert("Copy failed. Please select and copy manually.");
        }
      });
    });
  };

  document.addEventListener("DOMContentLoaded", initCopyButtons);
})();
