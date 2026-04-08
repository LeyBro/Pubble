document.addEventListener("DOMContentLoaded", function () {
    const themeButtons = document.querySelectorAll("#themeSwitcher button");
    const html = document.documentElement;

    function applyTheme(theme) {
        html.setAttribute("data-theme", theme);
        localStorage.setItem("pubble_theme", theme);

        themeButtons.forEach((btn) => {
            btn.classList.toggle("active-theme", btn.dataset.theme === theme);
        });
    }

    const savedTheme = localStorage.getItem("pubble_theme") || html.getAttribute("data-theme") || "dark";
    applyTheme(savedTheme);

    themeButtons.forEach((btn) => {
        btn.addEventListener("click", function () {
            applyTheme(btn.dataset.theme);
        });
    });

    document.querySelectorAll("[data-utc-time]").forEach((el) => {
        const raw = el.getAttribute("data-utc-time");
        if (!raw) return;

        const normalized = raw.replace(" ", "T");
        const date = new Date(normalized + "Z");

        if (!isNaN(date.getTime())) {
            el.textContent = date.toLocaleString("ru-RU", {
                year: "numeric",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit"
            });
        }
    });

    document.querySelectorAll("form[method='POST']").forEach((form) => {
        form.addEventListener("submit", function () {
            sessionStorage.setItem("pubble_scroll_path", location.pathname + location.search);
            sessionStorage.setItem("pubble_scroll_y", String(window.scrollY));
        });
    });

    const savedPath = sessionStorage.getItem("pubble_scroll_path");
    const savedY = sessionStorage.getItem("pubble_scroll_y");

    if (savedPath === location.pathname + location.search && savedY !== null) {
        requestAnimationFrame(() => {
            window.scrollTo(0, parseInt(savedY, 10) || 0);
            sessionStorage.removeItem("pubble_scroll_path");
            sessionStorage.removeItem("pubble_scroll_y");
        });
    }
});