"use strict";

(function initializePasswordRecovery() {
    const initialize = () => {
        const byId = id => document.getElementById(id);
        const status = byId("recoveryStatus");
        const emailKey = "riceMill.passwordRecoveryEmail";
        const tokenKey = "riceMill.passwordResetAuthorization";
        const cooldownKey = "riceMill.passwordRecoveryResendAfter";
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

        function storageGet(key) {
            try {
                return sessionStorage.getItem(key);
            } catch (_) {
                return null;
            }
        }
        function storageSet(key, value) {
            try {
                sessionStorage.setItem(key, value);
                return true;
            } catch (_) {
                return false;
            }
        }
        function storageRemove(key) {
            try {
                sessionStorage.removeItem(key);
            } catch (_) { /* Storage may be disabled. */}
        }

        function show(message, state = "") {
            if (!status) return;
            status.textContent = message;
            status.dataset.state = state;
        }

        async function post(path, payload) {
            const response = await fetch(path, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
                body: JSON.stringify(payload)
            });
            let data;
            try {
                data = await response.json();
            } catch (_) {
                data = {};
            }
            if (!response.ok || data.success === false) {
                throw new Error(data.message || "The request could not be completed. Please try again.");
            }
            return data;
        }

        function setSubmitting(button, active, label) {
            if (!button) return;
            button.disabled = active;
            if (active) {
                button.dataset.originalLabel = button.textContent;
                button.textContent = label;
            } else if (button.dataset.originalLabel) {
                button.textContent = button.dataset.originalLabel;
                delete button.dataset.originalLabel;
            }
        }

        const verifyForm = byId("verifyOtpForm");
        if (verifyForm) {
            let email = storageGet(emailKey) || "";
            const emailField = byId("recoveryEmailField");
            const emailInput = verifyForm.elements.recovery_email;
            const otpInput = verifyForm.elements.otp;
            const verifyButton = byId("verifyOtpSubmit");
            const resendButton = byId("resendOtpButton");
            if (!email) {
                if (emailField) emailField.hidden = false;
                if (emailInput) emailInput.required = true;
                show("Enter the email address used to request your verification code.");
            }

            verifyForm.addEventListener("submit", async event => {
                event.preventDefault();
                email = email || (emailInput?.value || "").trim();
                if (!email) {
                    show("Enter the email address used to request your verification code.", "error");
                    return;
                }
                setSubmitting(verifyButton, true, "Verifying…");
                show("Verifying your code…");
                try {
                    const result = await post("/api/auth/verify-otp", { email, otp: otpInput.value.trim() });
                    storageSet(tokenKey, result.resetToken);
                    show(result.message, "success");
                    window.location.assign("/reset-password");
                } catch (error) {
                    show(error instanceof TypeError
                        ? "We could not reach the recovery service. Check your connection and try again."
                        : error.message, "error");
                } finally {
                    setSubmitting(verifyButton, false);
                }
            });

            if (resendButton) {
                const updateCooldown = () => {
                    const remaining = Math.max(0, Number(storageGet(cooldownKey) || 0) - Date.now());
                    resendButton.disabled = !email || remaining > 0;
                    resendButton.textContent = remaining > 0 ? `Resend code (${Math.ceil(remaining / 1000)}s)` : "Resend code";
                    if (remaining > 0) window.setTimeout(updateCooldown, Math.min(remaining, 1000));
                };
                updateCooldown();
                resendButton.addEventListener("click", async() => {
                    if (!email || resendButton.disabled) return;
                    setSubmitting(resendButton, true, "Requesting…");
                    show("Submitting your request…");
                    try {
                        const result = await post("/api/auth/forgot-password/resend", { email });
                        storageSet(cooldownKey, String(Date.now() + 60000));
                        show(result.message, "success");
                    } catch (error) {
                        show(error instanceof TypeError
                            ? "We could not reach the recovery service. Check your connection and try again."
                            : error.message, "error");
                    } finally {
                        setSubmitting(resendButton, false);
                        updateCooldown();
                    }
                });
            }
        }

        const resetForm = byId("resetPasswordForm");
        if (resetForm) {
            const resetToken = storageGet(tokenKey) || "";
            const button = byId("resetPasswordSubmit");
            if (!resetToken) {
                show("Reset authorization is missing or expired. Request a new verification code.", "error");
                if (button) button.disabled = true;
            }
            resetForm.addEventListener("submit", async event => {
                event.preventDefault();
                if (!resetToken) return;
                const newPassword = byId("newPassword").value;
                const confirmPassword = byId("confirmPassword").value;
                if (newPassword !== confirmPassword) {
                    show("Passwords do not match.", "error");
                    return;
                }
                setSubmitting(button, true, "Resetting…");
                show("Updating your password…");
                try {
                    const result = await post("/api/auth/reset-password", {
                        resetToken, newPassword, confirmPassword
                    });
                    storageRemove(tokenKey);
                    storageRemove(emailKey);
                    storageRemove(cooldownKey);
                    resetForm.reset();
                    show(result.message, "success");
                    window.setTimeout(() => {
                        window.location.href = "/login";
                    }, 1200);
                } catch (error) {
                    show(error instanceof TypeError
                        ? "We could not reach the recovery service. Check your connection and try again."
                        : error.message, "error");
                } finally {
                    setSubmitting(button, false);
                }
            });
        }
    };

    // The asset is normally deferred, but initialize immediately if it is loaded
    // after DOMContentLoaded (for example, by a browser cache or page enhancer).
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();
