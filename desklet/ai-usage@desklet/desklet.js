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
};


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
        this._providerCards = {};
        this._root = null;
        this._tooltips = [];
        this._removed = false;
        this._settingsReady = false;
        this._stateReadCount = 0;
        this._countdownTickCount = 0;
        this._lastOpenedUri = null;

        this.cardStyle = "dark";
        this.density = "compact";
        this.contentWidth = DEFAULT_CONTENT_WIDTH;
        this.showClaude = true;
        this.showChatgpt = true;
        this.showCredits = true;
        this.showInactive = true;
        this.timeFormat = "24h";

        this.metadata["prevent-decorations"] = true;
        this._updateDecoration();

        this.settings = new Settings.DeskletSettings(
            this,
            this.metadata.uuid,
            deskletId
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "card-style",
            "cardStyle",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "density",
            "density",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "content-width",
            "contentWidth",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "show-claude",
            "showClaude",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "show-chatgpt",
            "showChatgpt",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "show-credits",
            "showCredits",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "show-inactive",
            "showInactive",
            this._onSettingChanged
        );
        this.settings.bindProperty(
            Settings.BindingDirection.IN,
            "time-format",
            "timeFormat",
            this._onSettingChanged
        );
        this._settingsReady = true;
        this._refresh();
        this._scheduleCountdownTick();
    },

    _onSettingChanged: function () {
        if (!this._settingsReady || this._removed)
            return;
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
        if (!valid) {
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
        this._countdownTickCount += 1;
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
        let labelRow = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-label-row",
        });
        labelRow.add(this._label(bar.label || "Limit", "aiu-label"));
        let resettingPill = this._label("resetting…", "aiu-resetting-pill");
        labelRow.add(resettingPill);
        labelRow.add(new St.Widget({ x_expand: true }));
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

        let resetRow = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-countdown-row",
        });
        resetRow.add(new St.Widget({ x_expand: true }));
        let countdownLabel = this._label("", "aiu-countdown");
        resetRow.add(countdownLabel);
        box.add(resetRow);

        let countdownEntry = {
            label: countdownLabel,
            pill: resettingPill,
            resetsAt: Number(bar.resets_at),
        };
        this._countdownLabels.push(countdownEntry);
        this._updateCountdownEntry(countdownEntry, now);
        return box;
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
                return `${label} resets ${this._formatAbsoluteTime(bar.resets_at)}`;
            });
        lines.push(this._creditsTooltipText(provider.credits));
        if (provider.error)
            lines.push(`Error: ${provider.error}`);
        return lines.join("\n");
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
            this._lastOpenedUri = uri;
        } catch (error) {
            global.logError(
                `AI Usage desklet browser launch: ${error.message || error}`
            );
        }
    },

    _providerCard: function (provider, now, index) {
        let stale = provider.stale === true || provider.ok === false;
        let classes = ["aiu-card", this._cardStyleClass()];
        if (index > 0)
            classes.push("aiu-card-spaced");
        if (stale)
            classes.push("aiu-card-stale");

        let card = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            reactive: true,
            track_hover: true,
            can_focus: true,
            style_class: classes.join(" "),
        });
        card.connect("button-release-event", () => {
            this._openProvider(provider.id);
            return Clutter.EVENT_STOP;
        });
        this._providerCards[provider.id] = card;

        let header = new St.BoxLayout({
            vertical: false,
            width: this._trackWidth(),
            style_class: "aiu-provider-header",
        });
        let plan = provider.plan && provider.plan !== "Unknown" ? ` · ${provider.plan}` : "";
        header.add(this._label(
            `${provider.label || provider.id || "Provider"}${plan}`,
            "aiu-provider-name"
        ));
        if (provider.ok === false) {
            header.add(this._label(
                provider.error_short || "unavailable",
                "aiu-error-pill"
            ));
        }
        card.add(header);

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
        card.add(barsBox);

        if (this.showCredits) {
            let credits = provider.credits;
            let creditSeverity = this._severity(
                credits && typeof credits === "object" ? credits.severity : "normal"
            );
            this._addRow(
                card,
                "Credits",
                this._creditsText(credits),
                "aiu-label",
                `aiu-credit-value aiu-severity-${creditSeverity}`,
                "aiu-credits-row"
            );
        }

        card.add(this._label(
            this._formatAge(provider.fetched_at, now),
            "aiu-footer"
        ));

        let tooltip = new Tooltips.Tooltip(
            card,
            this._tooltipText(provider, bars)
        );
        this._tooltips.push({
            providerId: provider.id,
            tooltip,
        });
        return card;
    },

    _render: function (state) {
        this._tooltips = [];
        this._countdownLabels = [];
        this._providerCards = {};
        let root = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-root ${this._densityClass()}`,
        });
        root.add(this._label("AI Usage", "aiu-title"));

        let now = Math.floor(Date.now() / 1000);
        let providers = state && Array.isArray(state.providers)
            ? state.providers.filter(provider => {
                if (!provider || typeof provider !== "object")
                    return false;
                if (provider.id === "claude")
                    return this.showClaude;
                if (provider.id === "chatgpt")
                    return this.showChatgpt;
                return true;
            })
            : [];
        if (providers.length === 0) {
            root.add(this._label("No providers shown", "aiu-waiting"));
        } else {
            providers.forEach((provider, index) => {
                if (provider && typeof provider === "object")
                    root.add(this._providerCard(provider, now, index));
            });
        }

        if (this._root)
            this._root.destroy();
        this._root = root;
        this.setContent(root);
    },

    _renderError: function (message) {
        this._tooltips = [];
        this._countdownLabels = [];
        this._providerCards = {};
        let root = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-root ${this._densityClass()} aiu-error-root`,
        });
        root.add(this._label("AI Usage", "aiu-title"));
        let card = new St.BoxLayout({
            vertical: true,
            width: this._contentWidth(),
            style_class: `aiu-card ${this._cardStyleClass()}`,
        });
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
            this._stateReadCount += 1;
            let [ok, contents] = GLib.file_get_contents(STATE_PATH);
            if (!ok)
                throw new Error("state.json could not be read");
            let state = JSON.parse(ByteArray.toString(contents));
            if (!state || state.schema !== 1 || !Array.isArray(state.providers))
                throw new Error("state.json has an unsupported schema");
            this._render(state);
        } catch (error) {
            global.logError(`AI Usage desklet: ${error.message || error}`);
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
