const ByteArray = imports.byteArray;
const Clutter = imports.gi.Clutter;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Mainloop = imports.mainloop;
const Pango = imports.gi.Pango;
const St = imports.gi.St;

const Desklet = imports.ui.desklet;
const Settings = imports.ui.settings;
const Tooltips = imports.ui.tooltips;

const STATE_PATH = GLib.build_filenamev([
    GLib.get_home_dir(),
    ".cache",
    "ai-usage-desklet",
    "state.json",
]);
const REFRESH_SECONDS = 30;
const DEFAULT_CONTENT_WIDTH = 360;
const SEVERITIES = ["normal", "warning", "critical"];
const PROVIDER_URIS = {
    claude: "https://claude.ai/settings/usage",
    chatgpt: "https://chatgpt.com/#settings/Account",
    openrouter: "https://openrouter.ai/credits",
    opencode: "https://opencode.ai/auth",
    mistral: "https://admin.mistral.ai/organization/usage",
};
// How to sign in again, per provider. Written for someone who does not live in a
// terminal, so it names the command rather than assuming it.
const PROVIDER_SIGNIN = {
    claude: "Open a terminal, type  claude  and press enter, then type  /login",
    chatgpt: "Open a terminal, type  codex login  and press enter",
    openrouter: "Add your OpenRouter key to  ~/.local/share/ai-usage-desklet/openrouter.env",
    opencode: "Add your OpenCode Go key to  ~/.local/share/ai-usage-desklet/opencode.env",
    mistral: "In a terminal: vibe, then sign in",
};
// Index of the Help page in settings-schema.json's page list.
const HELP_PAGE_INDEX = 2;
// The card's section order, matching the XFCE AI Usage card.
const PROVIDER_ORDER = ["claude", "chatgpt", "opencode", "mistral", "openrouter"];
// Settings key to property, all bound identically.
const SETTING_BINDINGS = [
    ["card-style", "cardStyle"],
    ["density", "density"],
    ["content-width", "contentWidth"],
    ["show-claude", "showClaude"],
    ["show-chatgpt", "showChatgpt"],
    ["show-openrouter", "showOpenrouter"],
    ["show-opencode", "showOpencode"],
    ["show-mistral", "showMistral"],
    ["show-credits", "showCredits"],
    ["show-inactive", "showInactive"],
    ["show-help", "showHelp"],
    ["time-format", "timeFormat"],
];


function AIUsageDesklet(metadata, deskletId) {
    this._init(metadata, deskletId);
}


AIUsageDesklet.prototype = {
    __proto__: Desklet.Desklet.prototype,

    _init: function (metadata, deskletId) {
        Desklet.Desklet.prototype._init.call(this, metadata, deskletId);
        this._timeoutId = null;
        this._countdownTimeoutId = null;
        this._countdownLabels = [];
        this._footerLabels = [];
        this._root = null;
        this._removed = false;
        this._settingsReady = false;
        // The providers portion of the last rendered state, used to skip a full
        // rebuild when nothing changed, so an open tooltip is not torn down.
        this._renderedKey = null;

        this.cardStyle = "dark";
        this.density = "compact";
        this.contentWidth = DEFAULT_CONTENT_WIDTH;
        this.showClaude = true;
        this.showChatgpt = true;
        this.showOpenrouter = true;
        this.showOpencode = true;
        this.showMistral = true;
        this.showCredits = true;
        this.showInactive = true;
        this.showHelp = true;
        this.timeFormat = "24h";

        this.metadata["prevent-decorations"] = true;
        this._updateDecoration();

        this.settings = new Settings.DeskletSettings(
            this,
            this.metadata.uuid,
            deskletId
        );
        SETTING_BINDINGS.forEach(([key, property]) => {
            this.settings.bindProperty(
                Settings.BindingDirection.IN,
                key,
                property,
                this._onSettingChanged
            );
        });
        this._settingsReady = true;
        this._refresh();
        this._scheduleCountdownTick();
    },

    _onSettingChanged: function () {
        if (!this._settingsReady || this._removed)
            return;
        this._renderedKey = null;
        this._refresh();
    },

    _contentWidth: function () {
        return Math.min(480, Math.max(260, Number(this.contentWidth) || DEFAULT_CONTENT_WIDTH));
    },

    _trackWidth: function () {
        return this._contentWidth() - 36;
    },

    _cardStyleClass: function () {
        return ["dark", "light", "solid"].indexOf(this.cardStyle) >= 0
            ? `aiu-card-${this.cardStyle}`
            : "aiu-card-dark";
    },

    _densityClass: function () {
        return this.density === "standard"
            ? "aiu-density-standard"
            : "aiu-density-compact";
    },

    _label: function (text, styleClass) {
        let label = new St.Label({
            text: String(text),
            style_class: styleClass || "aiu-label",
        });
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        label.clutter_text.line_wrap = false;
        return label;
    },

    _severity: function (value) {
        return SEVERITIES.indexOf(value) >= 0 ? value : "normal";
    },

    _addRow: function (
        container,
        leftText,
        rightText,
        leftClass,
        rightClass,
        rowClass
    ) {
        let row = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: rowClass || "aiu-label-row",
        });
        row.add(this._label(leftText, leftClass || "aiu-label"));
        row.add(new St.Widget({ x_expand: true }));
        row.add(this._label(rightText, rightClass || "aiu-value"));
        container.add(row);
        return row;
    },

    _formatDuration: function (seconds) {
        let remaining = Math.max(0, Math.floor(Number(seconds) || 0));
        if (remaining < 60)
            return `${remaining}s`;
        if (remaining < 3600)
            return `${Math.floor(remaining / 60)}m`;
        if (remaining < 86400)
            return `${Math.floor(remaining / 3600)}h ${Math.floor((remaining % 3600) / 60)}m`;
        return `${Math.floor(remaining / 86400)}d ${Math.floor((remaining % 86400) / 3600)}h`;
    },

    _formatAge: function (fetchedAt, now) {
        let timestamp = Number(fetchedAt);
        if (!Number.isFinite(timestamp) || timestamp <= 0)
            return "never updated";
        let age = Math.max(0, Math.floor(now - timestamp));
        if (age < 60)
            return `updated ${age}s ago`;
        if (age < 3600)
            return `updated ${Math.floor(age / 60)}m ago`;
        if (age < 86400)
            return `updated ${Math.floor(age / 3600)}h ago`;
        return `updated ${Math.floor(age / 86400)}d ago`;
    },

    _creditsText: function (credits) {
        if (!credits || typeof credits !== "object")
            return "unavailable";
        if (credits.short_text !== undefined && credits.short_text !== null)
            return String(credits.short_text);
        if (credits.kind === "money")
            return credits.enabled ? String(credits.used_text || "—") : "off";
        if (credits.unlimited)
            return "unlimited";
        return credits.balance_text !== undefined ? String(credits.balance_text) : "0";
    },

    _barsForProvider: function (provider) {
        let bars = Array.isArray(provider.bars) ? provider.bars : [];
        if (!this.showInactive)
            bars = bars.filter(bar => !bar || bar.active !== false);
        return bars;
    },

    _updateCountdownEntry: function (entry, now) {
        let resetAt = Number(entry.resetsAt);
        let valid = Number.isFinite(resetAt) && resetAt > 0;
        let resetting = valid && now >= resetAt;
        entry.pill.visible = resetting;
        if (entry.noReset) {
            entry.label.set_text("");
        } else if (!valid) {
            entry.label.set_text("reset unavailable");
        } else if (resetting) {
            entry.label.set_text(" ");
        } else {
            entry.label.set_text(
                `resets in ${this._formatDuration(resetAt - now)}`
            );
        }
    },

    _updateCountdowns: function () {
        let now = Math.floor(Date.now() / 1000);
        this._countdownLabels.forEach(entry => {
            if (entry.label && !entry.label.is_finalized())
                this._updateCountdownEntry(entry, now);
        });
        this._footerLabels.forEach(entry => {
            if (entry.label && !entry.label.is_finalized())
                entry.label.set_text(this._formatAge(entry.fetchedAt, now));
        });
    },

    _bar: function (bar, now, index) {
        let box = new St.BoxLayout({
            vertical: true,
            style_class: index === 0
                ? "aiu-bar-group"
                : "aiu-bar-group aiu-bar-group-spaced",
        });
        let pct = Math.min(100, Math.max(0, Number(bar.percent) || 0));
        let severity = this._severity(bar.severity);
        // One row above the meter, as the XFCE card draws it: the window name
        // and pill on the left, the countdown and percentage on the right.
        let labelRow = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-label-row",
        });
        labelRow.add(this._label(bar.label || "Limit", "aiu-label aiu-bar-label"));
        let resettingPill = this._label("resetting…", "aiu-resetting-pill");
        labelRow.add(resettingPill);
        labelRow.add(new St.Widget({ x_expand: true }));
        let countdownLabel = this._label("", "aiu-countdown");
        labelRow.add(countdownLabel);
        labelRow.add(this._label(
            `${Math.round(pct)}%`,
            `aiu-percent aiu-severity-${severity}`
        ));
        box.add(labelRow);

        let track = new St.Widget({
            width: this._trackWidth(),
            style_class: "aiu-bar-track",
        });
        let computedWidth = Math.round(this._trackWidth() * pct / 100);
        let fillWidth = pct === 0 ? 0 : Math.max(2, computedWidth);
        let scaleFactor = St.ThemeContext.get_for_stage(global.stage).scale_factor || 1;
        let cssWidth = fillWidth / scaleFactor;
        let fill = new St.Widget({
            style_class: `aiu-bar-fill aiu-severity-${severity}`,
        });
        fill.set_style(`width: ${cssWidth}px;`);
        track.add_child(fill);
        box.add(track);

        let countdownEntry = {
            label: countdownLabel,
            pill: resettingPill,
            resetsAt: Number(bar.resets_at),
            // An explicit null means the provider publishes no reset instant at
            // all, which is not the same as one we failed to read.
            noReset: bar.resets_at === null,
        };
        this._countdownLabels.push(countdownEntry);
        this._updateCountdownEntry(countdownEntry, now);
        return box;
    },

    // The plan's clock-over date, in the poller's own words. The poller only
    // publishes renews_at where the provider returns a real field, so an
    // absent value means no date was ever guessed at.
    _renewalText: function (provider) {
        let renewsAt = Number(provider.renews_at);
        if (!Number.isFinite(renewsAt) || renewsAt <= 0)
            return "";
        let date = GLib.DateTime.new_from_unix_local(Math.round(renewsAt));
        let now = GLib.DateTime.new_now_local();
        let pattern = date.get_year() === now.get_year() ? "%e %b" : "%e %b %Y";
        let text = (date.format(pattern) || "").replace(/\s+/g, " ").trim();
        let period = provider.renews_period;
        return period ? `renews ${text}, ${period}` : `renews ${text}`;
    },

    _formatAbsoluteTime: function (timestamp) {
        let epoch = Math.round(Number(timestamp));
        if (!Number.isFinite(epoch) || epoch <= 0)
            return "unknown time";
        let date = GLib.DateTime.new_from_unix_local(epoch);
        let now = GLib.DateTime.new_now_local();
        let includeDate = date.format("%Y-%m-%d") !== now.format("%Y-%m-%d");
        let use12Hour = this.timeFormat === "12h";
        let pattern;
        if (includeDate)
            pattern = use12Hour ? "%a %e %b %I:%M %p" : "%a %e %b %H:%M";
        else
            pattern = use12Hour ? "%I:%M %p" : "%H:%M";
        let text = date.format(pattern) || "unknown time";
        text = text.replace(/\s+/g, " ").trim();
        return use12Hour
            ? text.replace(/(^| )0(\d:\d\d)/, "$1$2")
            : text;
    },

    _creditsTooltipText: function (credits) {
        if (!credits || typeof credits !== "object")
            return "Credits: unavailable";
        if (credits.kind === "money") {
            let used = credits.used_text || "—";
            let limit = credits.limit_text;
            return limit ? `Credits: ${used} / ${limit}` : `Credits: ${used}`;
        }
        if (credits.unlimited)
            return "Credits: unlimited";
        let balance = credits.balance_text !== undefined
            ? credits.balance_text
            : "0";
        return `Credits: ${balance}`;
    },

    _tooltipText: function (provider, bars) {
        let lines = bars
            .filter(bar => bar && typeof bar === "object")
            .map(bar => {
                let label = bar.label || "Limit";
                if (bar.resets_at === null)
                    return `${label}: no reset time published`;
                return `${label} resets ${this._formatAbsoluteTime(bar.resets_at)}`;
            });
        lines.push(this._creditsTooltipText(provider.credits));
        if (provider.error)
            lines.push(`Error: ${provider.error}`);
        return lines.join("\n");
    },

    _isResetting: function (bars, now) {
        return bars.some(bar => {
            if (!bar || typeof bar !== "object")
                return false;
            let resetAt = Number(bar.resets_at);
            return Number.isFinite(resetAt) && resetAt > 0 && now >= resetAt;
        });
    },

    _nextAttemptText: function (provider, now) {
        let next = Number(provider.next_attempt_at);
        if (!Number.isFinite(next) || next <= 0)
            return "";
        let when = this._formatAbsoluteTime(next);
        return next > now
            ? `${when}, ${this._formatDuration(next - now)} away`
            : when;
    },

    /* Plain-language account of what this card is showing, why, and what to do
     * about it. States are tested in order of urgency and the first match wins;
     * anything unrecognised falls through to the healthy text rather than
     * inventing a remedy. */
    _helpText: function (provider, bars, now) {
        let label = provider.label || provider.id || "Provider";
        let plan = provider.plan && provider.plan !== "Unknown"
            ? provider.plan
            : "plan unknown";
        let fetched = this._formatAbsoluteTime(provider.fetched_at);
        let signin = PROVIDER_SIGNIN[provider.id]
            || "Sign in to the provider again";
        let nextAttempt = this._nextAttemptText(provider, now);
        let nextLine = nextAttempt
            ? `Next attempt: ${nextAttempt}.`
            : "Next attempt: as soon as the poller runs.";
        let errorShort = String(provider.error_short || "").toLowerCase();
        let lines = [];

        if (provider.auth_state === "expired") {
            lines.push(`${label}: the sign-in has expired, so these figures are`);
            lines.push(`frozen at ${fetched} and will not move.`);
            lines.push("");
            lines.push(`Fix it: ${signin}`);
            lines.push(`The card then recovers on its own. ${nextLine}`);
        } else if (provider.ok === false && errorShort.indexOf("rate") >= 0) {
            lines.push(`${label}: the provider is throttling the usage endpoint,`);
            lines.push("which is normal and needs nothing from you.");
            lines.push("");
            lines.push(`Showing the last good figures, from ${fetched}.`);
            lines.push(nextLine);
        } else if (provider.ok === false) {
            lines.push(
                `${label}: could not reach the provider (${provider.error_short || "reason unknown"}).`
            );
            lines.push("");
            lines.push(`Showing the last good figures, from ${fetched}.`);
            lines.push(nextLine);
            lines.push("If this persists, check the network first, then run:");
            lines.push("python3 ~/.local/share/ai-usage-desklet/poller.py --once --print");
        } else if (this._isResetting(bars, now)) {
            lines.push(`${label} · ${plan}: a limit window has just rolled over.`);
            lines.push("");
            lines.push("Holding the previous figure rather than guessing at zero,");
            lines.push(`until the next successful check. ${nextLine}`);
        } else if (provider.plan_source !== "profile" && provider.plan_source !== "api") {
            lines.push(`${label}: the plan is shown as ${plan}, but that was read`);
            lines.push("from the sign-in file on this computer rather than from");
            lines.push("the account, so it can lag a plan change by weeks.");
            lines.push("");
            lines.push(`To refresh it: ${signin}`);
            lines.push("");
            lines.push("The percentages are unaffected. They are measured by the");
            lines.push("provider against your real plan either way.");
        } else {
            lines.push(`${label} · ${plan}, ${this._formatAge(provider.fetched_at, now)}.`);
            lines.push("Plan confirmed live against the account.");
            bars.forEach(bar => {
                if (bar && typeof bar === "object") {
                    lines.push(
                        bar.resets_at === null
                            ? `${bar.label || "Limit"} has no reset time published.`
                            : `${bar.label || "Limit"} resets ${this._formatAbsoluteTime(bar.resets_at)}.`
                    );
                }
            });
            lines.push(nextLine);
        }

        lines.push("");
        lines.push("Click this ? for settings and the full help.");
        return lines.join("\n");
    },

    _openHelpSettings: function () {
        try {
            this.configureDesklet(HELP_PAGE_INDEX);
        } catch (error) {
            global.logError(
                `AI Usage desklet settings launch: ${error.message || error}`
            );
        }
    },

    _openProvider: function (providerId) {
        let uri = PROVIDER_URIS[providerId];
        if (!uri)
            return;
        try {
            Gio.app_info_launch_default_for_uri(
                uri,
                global.create_app_launch_context()
            );
        } catch (error) {
            global.logError(
                `AI Usage desklet browser launch: ${error.message || error}`
            );
        }
    },

    _providerSection: function (provider, now) {
        let stale = provider.stale === true || provider.ok === false;
        let section = new St.BoxLayout({
            vertical: true,
            width: this._trackWidth(),
            reactive: true,
            track_hover: true,
            can_focus: true,
            style_class: stale ? "aiu-section aiu-section-stale" : "aiu-section",
        });
        section.connect("button-release-event", () => {
            this._openProvider(provider.id);
            return Clutter.EVENT_STOP;
        });

        let header = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-provider-header",
        });
        header.add(this._label(
            provider.label || provider.id || "Provider",
            "aiu-provider-name"
        ));
        // The plan sits in its own pill beside the name, and the renewal date
        // beside the pill, exactly as the XFCE card draws them. The poller only
        // publishes a renewal where the provider returns a real field.
        if (provider.plan && provider.plan !== "Unknown")
            header.add(this._label(provider.plan, "aiu-plan-pill"));
        let renewal = this._renewalText(provider);
        if (renewal)
            header.add(this._label(renewal, "aiu-renews"));
        header.add(new St.Widget({ x_expand: true }));
        if (provider.ok === false) {
            header.add(this._label(
                provider.error_short || "unavailable",
                "aiu-error-pill"
            ));
        }
        let footer = this._label(
            this._formatAge(provider.fetched_at, now),
            "aiu-footer"
        );
        header.add(footer);
        this._footerLabels.push({
            label: footer,
            fetchedAt: Number(provider.fetched_at),
            providerId: provider.id,
        });
        // The help marker sits at the very right of the header, so the row is
        // unchanged when help is off apart from the marker itself.
        let helpIcon = null;
        if (this.showHelp) {
            helpIcon = this._label("?", "aiu-help");
            helpIcon.reactive = true;
            helpIcon.track_hover = true;
            helpIcon.can_focus = true;
            helpIcon.connect("button-release-event", () => {
                this._openHelpSettings();
                // Stop here, or the click also falls through to the section and
                // opens the provider's usage page in a browser.
                return Clutter.EVENT_STOP;
            });
            header.add(helpIcon);
        }
        section.add(header);

        let barsBox = new St.BoxLayout({
            vertical: true,
            style_class: "aiu-bars",
        });
        let bars = this._barsForProvider(provider);
        if (bars.length === 0) {
            barsBox.add(this._label("Usage unavailable", "aiu-unavailable"));
        } else {
            bars.forEach((bar, barIndex) => {
                if (bar && typeof bar === "object")
                    barsBox.add(this._bar(bar, now, barIndex));
            });
        }
        section.add(barsBox);

        if (this.showCredits) {
            let credits = provider.credits;
            let creditSeverity = this._severity(
                credits && typeof credits === "object" ? credits.severity : "normal"
            );
            this._addRow(
                section,
                "Credits",
                this._creditsText(credits),
                "aiu-label",
                `aiu-credit-value aiu-severity-${creditSeverity}`,
                "aiu-credits-row"
            );
        }

        let tooltip = new Tooltips.Tooltip(
            section,
            this._tooltipText(provider, bars)
        );

        if (helpIcon) {
            new Tooltips.Tooltip(
                helpIcon,
                this._helpText(provider, bars, now)
            );
            // Cinnamon has no tooltip manager, and the pointer is still inside
            // the section while it is over the ?, so both tooltips would show at
            // once. Mute the section's while the marker is hovered.
            helpIcon.connect("enter-event", () => {
                tooltip.preventShow = true;
                tooltip.hide();
                return Clutter.EVENT_PROPAGATE;
            });
            helpIcon.connect("leave-event", () => {
                tooltip.preventShow = false;
                return Clutter.EVENT_PROPAGATE;
            });
        }
        return section;
    },

    _render: function (state) {
        this._countdownLabels = [];
        this._footerLabels = [];
        let root = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-root ${this._densityClass()}`,
        });

        let now = Math.floor(Date.now() / 1000);
        let providers = state && Array.isArray(state.providers)
            ? state.providers.filter(provider => {
                if (!provider || typeof provider !== "object")
                    return false;
                if (provider.id === "claude")
                    return this.showClaude;
                if (provider.id === "chatgpt")
                    return this.showChatgpt;
                if (provider.id === "openrouter")
                    return this.showOpenrouter;
                if (provider.id === "opencode")
                    return this.showOpencode;
                if (provider.id === "mistral")
                    return this.showMistral;
                return true;
            })
            : [];
        if (providers.length === 0) {
            root.add(this._label("No providers shown", "aiu-waiting"));
        } else {
            // One card for the whole desklet, sections in the XFCE card's
            // order, separated by hairline rules.
            let rank = {};
            PROVIDER_ORDER.forEach((id, index) => { rank[id] = index; });
            providers.sort((a, b) => {
                let ra = rank[a.id] !== undefined ? rank[a.id] : PROVIDER_ORDER.length;
                let rb = rank[b.id] !== undefined ? rank[b.id] : PROVIDER_ORDER.length;
                return ra - rb;
            });
            let card = new St.BoxLayout({
                vertical: true,
                width: this._contentWidth(),
                style_class: `aiu-card ${this._cardStyleClass()}`,
            });
            let head = new St.BoxLayout({
                vertical: false,
                width: this._trackWidth(),
                style_class: "aiu-card-header",
            });
            head.add(this._label("●", "aiu-head-dot"));
            head.add(this._label("AI USAGE", "aiu-head"));
            card.add(head);
            providers.forEach((provider, index) => {
                if (index > 0)
                    card.add(this._rule());
                card.add(this._providerSection(provider, now));
            });
            root.add(card);
        }

        if (this._root)
            this._root.destroy();
        this._root = root;
        this.setContent(root);
    },

    _rule: function () {
        return new St.Widget({
            width: this._trackWidth(),
            style_class: "aiu-rule",
        });
    },

    _renderError: function (message) {
        this._countdownLabels = [];
        this._footerLabels = [];
        let root = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-root ${this._densityClass()} aiu-error-root`,
        });
        let card = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-card ${this._cardStyleClass()}`,
        });
        let head = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-card-header",
        });
        // The error view keeps the heading, because it is the only thing
        // saying which desklet the orphaned card belongs to.
        head.add(this._label("●", "aiu-head-dot"));
        head.add(this._label("AI USAGE", "aiu-head"));
        card.add(head);
        card.add(this._label(message, "aiu-unavailable"));
        root.add(card);
        if (this._root)
            this._root.destroy();
        this._root = root;
        this.setContent(root);
    },

    _scheduleRefresh: function () {
        if (this._removed)
            return;
        if (this._timeoutId)
            Mainloop.source_remove(this._timeoutId);
        this._timeoutId = Mainloop.timeout_add_seconds(REFRESH_SECONDS, () => {
            this._timeoutId = null;
            this._refresh();
            return false;
        });
    },

    _scheduleCountdownTick: function () {
        if (this._removed || this._countdownTimeoutId)
            return;
        this._countdownTimeoutId = Mainloop.timeout_add_seconds(1, () => {
            if (this._removed) {
                this._countdownTimeoutId = null;
                return false;
            }
            try {
                this._updateCountdowns();
            } catch (error) {
                global.logError(
                    `AI Usage desklet countdown: ${error.message || error}`
                );
            }
            return true;
        });
    },

    _refresh: function () {
        try {
            let [ok, contents] = GLib.file_get_contents(STATE_PATH);
            if (!ok)
                throw new Error("state.json could not be read");
            let state = JSON.parse(ByteArray.toString(contents));
            if (!state || state.schema !== 1 || !Array.isArray(state.providers))
                throw new Error("state.json has an unsupported schema");
            // Only visible content should force a rebuild: generated_at moves
            // every poll, and fetched_at / next_attempt_at move on every
            // successful one, while the footer age is kept live by the
            // one-second tick instead.
            let key = JSON.stringify(state.providers.map(provider =>
                provider && typeof provider === "object"
                    ? Object.assign({}, provider, {
                        fetched_at: 0,
                        next_attempt_at: 0,
                    })
                    : provider));
            if (key !== this._renderedKey || !this._root) {
                this._render(state);
                this._renderedKey = key;
            } else {
                let byId = {};
                state.providers.forEach(provider => {
                    if (provider && typeof provider === "object")
                        byId[provider.id] = provider;
                });
                this._footerLabels.forEach(entry => {
                    let provider = byId[entry.providerId];
                    if (provider)
                        entry.fetchedAt = Number(provider.fetched_at);
                });
            }
        } catch (error) {
            global.logError(`AI Usage desklet: ${error.message || error}`);
            this._renderedKey = null;
            try {
                this._renderError("Usage data unavailable");
            } catch (renderError) {
                global.logError(
                    `AI Usage desklet error renderer: ${renderError.message || renderError}`
                );
            }
        } finally {
            try {
                this._scheduleRefresh();
            } catch (scheduleError) {
                global.logError(
                    `AI Usage desklet refresh scheduler: ${scheduleError.message || scheduleError}`
                );
            }
        }
    },

    on_desklet_removed: function () {
        this._removed = true;
        if (this._timeoutId) {
            Mainloop.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        if (this._countdownTimeoutId) {
            Mainloop.source_remove(this._countdownTimeoutId);
            this._countdownTimeoutId = null;
        }
    },
};


function main(metadata, deskletId) {
    return new AIUsageDesklet(metadata, deskletId);
}
