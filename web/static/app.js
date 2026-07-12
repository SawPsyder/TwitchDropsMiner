// Twitch Drops Miner Web Client
// Socket.IO and API communication

// Global state
const state = {
    connected: false,
    channels: {},
    campaigns: {},
    settings: {},
    autoWatchGames: [],  // Library-detected games (recently played first), ranked below user picks
    ownedGames: [],  // Owned games synced from library providers (for the blacklist/whitelist picker)
    currentDrop: null,
    countdownTimer: null,  // Track the active countdown timer
    translations: {},  // Store current translations
    linkClickedCampaigns: new Set(),  // Campaign IDs where "Link Account" was clicked; shows a "Refresh Status" trigger
    linkClickedAutoGames: new Set(),  // Game names where "Link Account" was clicked in the unlinked auto-tracked panel
    unlinkedAutoItems: [],  // Latest "Games Awaiting Link" tree (kept in sync for post-refresh link checks)
    pendingLinkCheck: null,  // { kind: 'campaign'|'auto_game', campaignId?, gameName } - set right before a "Refresh Status" reload
    // Inventory section collapse state (Categories view) - active/not_linked/upcoming expanded by default
    inventorySections: { active: true, not_linked: true, upcoming: true, finished: false, expired: false }
};

// ==================== Animations / Reduced Motion ====================

// "auto" mirrors this media query; "on"/"off" (Settings > General > Animations)
// override it regardless of what the OS/browser reports.
const osReducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

function applyAnimationsMode(mode) {
    const effectiveReduced = mode === 'off' ? true : mode === 'on' ? false : osReducedMotionQuery.matches;
    document.body.classList.toggle('reduce-motion', effectiveReduced);
}

function getAnimationsModeFromUI() {
    if (document.getElementById('animations-on')?.checked) return 'on';
    if (document.getElementById('animations-off')?.checked) return 'off';
    return 'auto';
}

function setAnimationsModeUI(mode) {
    const value = mode || 'auto';
    const radio = document.getElementById(`animations-${value}`);
    if (radio) radio.checked = true;
    applyAnimationsMode(value);
}

// Live-react to OS-level changes while left on "auto", and apply a sane
// default immediately (before settings have loaded) to avoid a flash of motion.
osReducedMotionQuery.addEventListener('change', () => {
    if ((state.settings.animations || 'auto') === 'auto') {
        applyAnimationsMode('auto');
    }
});
applyAnimationsMode('auto');

// ==================== Dark Mode ====================

// "auto" mirrors this media query; "on"/"off" (Settings > Appearance > Dark Mode)
// override it regardless of what the OS/browser reports.
const osDarkModeQuery = window.matchMedia('(prefers-color-scheme: dark)');

function applyDarkMode(mode) {
    const effectiveDark = mode === 'on' ? true : mode === 'off' ? false : osDarkModeQuery.matches;
    document.body.classList.toggle('dark-mode', effectiveDark);
}

function getDarkModeFromUI() {
    if (document.getElementById('dark-mode-on')?.checked) return 'on';
    if (document.getElementById('dark-mode-off')?.checked) return 'off';
    return 'auto';
}

function setDarkModeUI(mode) {
    const value = mode || 'auto';
    const radio = document.getElementById(`dark-mode-${value}`);
    if (radio) radio.checked = true;
    applyDarkMode(value);
}

function getInventoryViewModeFromUI() {
    return document.getElementById('inventory-view-mode-category')?.checked ? 'category' : 'game';
}

function setInventoryViewModeUI(mode) {
    const value = mode === 'category' ? 'category' : 'game';
    const radio = document.getElementById(`inventory-view-mode-${value}`);
    if (radio) radio.checked = true;
}

// Live-react to OS-level changes while left on "auto", and apply a sane
// default immediately (before settings have loaded) to avoid a flash of the wrong theme.
osDarkModeQuery.addEventListener('change', () => {
    if ((state.settings.dark_mode || 'auto') === 'auto') {
        applyDarkMode('auto');
    }
});
applyDarkMode('auto');

// ==================== Value Sliders ====================
// Keeps the decorative fill/label of a .value-slider (Connection Quality,
// Minimum Refresh Interval) in sync with its underlying <input type="range">.

function updateSliderVisual(input) {
    const wrapper = input.closest('.value-slider');
    if (!wrapper) return;
    const min = parseFloat(input.min);
    const max = parseFloat(input.max);
    const value = parseFloat(input.value);
    const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
    const fill = wrapper.querySelector('.value-slider-fill');
    if (fill) fill.style.width = `${pct}%`;
    const label = wrapper.querySelector('.value-slider-value');
    if (label) label.textContent = input.value;
}

// ==================== Version Checking ====================

async function fetchAndDisplayVersion() {
    try {
        const response = await fetch('/api/version');
        if (!response.ok) throw new Error('Failed to fetch version');

        const data = await response.json();
        const versionElement = document.getElementById('current-version');
        if (versionElement) {
            let versionText = data.current_version;
            // Add (latest) indicator if we know the latest version and it matches
            if (data.latest_version && data.current_version === data.latest_version) {
                versionText += ' (latest)';
            }
            versionElement.textContent = versionText;

            // Translate footer version text
            const footerVersionText = document.getElementById('footer-version-text');
            if (footerVersionText && state.translations.gui?.footer) {
                const versionLabel = state.translations.gui.footer.version || 'Version:';
                // Preserve the span inside
                const span = footerVersionText.querySelector('span');
                footerVersionText.textContent = versionLabel + ' ';
                footerVersionText.appendChild(span);
            }
        }

        // Display update notification if available
        if (data.update_available && data.latest_version) {
            const updateIndicator = document.getElementById('footer-update-indicator');
            const latestVersionSpan = document.getElementById('latest-version');
            const updateLink = document.getElementById('footer-update-link');

            if (updateIndicator && latestVersionSpan && updateLink) {
                latestVersionSpan.textContent = data.latest_version;
                updateLink.href = data.download_url;
                updateIndicator.style.display = 'inline-block';

                // Translate update message
                if (state.translations.gui?.footer) {
                    const updateLabel = state.translations.gui.footer.update_available || 'Update Available:';
                    const linkText = document.createTextNode(` ⚠ ${updateLabel} `);
                    // Clear existing text nodes but keep the span
                    const span = updateLink.querySelector('span'); // latest-version span
                    updateLink.textContent = '';
                    updateLink.appendChild(linkText);
                    updateLink.appendChild(span);
                }

                // Log to console
                console.log(`Update available: ${data.latest_version} (current: ${data.current_version})`);
            }
        }
    } catch (error) {
        console.warn('Could not fetch version information:', error);
        // Set placeholder text if fetch fails
        const versionElement = document.getElementById('current-version');
        const loadingText = state.translations.gui?.footer?.loading || 'Loading...';
        if (versionElement && versionElement.textContent === loadingText) {
            versionElement.textContent = 'Unknown';
        }
    }
}

// Initialize Socket.IO connection
const socket = io({
    transports: ['websocket', 'polling'],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: Infinity
});

// ==================== Socket.IO Event Handlers ====================

socket.on('connect', () => {
    console.log('Connected to server');
    state.connected = true;
    const connText = state.translations.gui?.websocket?.connected || 'Connected';
    document.getElementById('connection-indicator').textContent = '● ' + connText;
    document.getElementById('connection-indicator').className = 'connected';
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
    state.connected = false;
    const disconnText = state.translations.gui?.websocket?.disconnected || 'Disconnected';
    document.getElementById('connection-indicator').textContent = '● ' + disconnText;
    document.getElementById('connection-indicator').className = 'disconnected';
});

socket.on('initial_state', (data) => {
    console.log('Received initial state', data);
    if (data.status) updateStatus(data.status);

    // Batch update channels to prevent UI freezing
    if (data.channels) {
        data.channels.forEach(ch => {
            state.channels[ch.id] = ch;
        });
        renderChannels();
    }

    // Batch update campaigns to prevent UI freezing
    if (data.campaigns) {
        data.campaigns.forEach(camp => {
            state.campaigns[camp.id] = camp;
        });
        renderInventory();
    }

    // Batch update console logs
    if (data.console) {
        const consoleEl = document.getElementById('console-output');
        const fragment = document.createDocumentFragment();
        data.console.forEach(line => {
            const div = document.createElement('div');
            div.textContent = line;
            fragment.appendChild(div);
        });
        consoleEl.appendChild(fragment);
        consoleEl.scrollTop = consoleEl.scrollHeight;
        while (consoleEl.children.length > 1000) {
            consoleEl.removeChild(consoleEl.firstChild);
        }
    }

    if (data.auto_watch_games) {
        state.autoWatchGames = data.auto_watch_games;
        renderAutoWatchList();
    }
    if (data.settings) updateSettingsUI(data.settings);
    if (data.login) updateLoginStatus(data.login);
    if (data.manual_mode) updateManualModeUI(data.manual_mode);
    // Restore current drop progress if it exists
    if (data.current_drop) {
        updateDropProgress(data.current_drop);
    } else {
        clearDropProgress();
    }

    if (data.wanted_items) {
        renderWantedItems(data.wanted_items);
    }
    if (data.unlinked_auto_items) {
        renderUnlinkedAutoItems(data.unlinked_auto_items);
    }
});

socket.on('status_update', (data) => {
    updateStatus(data.status);
});

socket.on('console_output', (data) => {
    addConsoleLine(data.message);
});

socket.on('channel_add', (data) => {
    updateChannel(data);
});

socket.on('channel_update', (data) => {
    updateChannel(data);
});

socket.on('channel_remove', (data) => {
    removeChannel(data.id);
});

socket.on('channels_clear', () => {
    clearChannels();
});

socket.on('channels_batch_update', (data) => {
    // Replace all channels atomically to prevent flickering
    state.channels = {};
    data.channels.forEach(ch => {
        state.channels[ch.id] = ch;
    });
    renderChannels();
});

socket.on('channel_watching', (data) => {
    setWatchingChannel(data.id);
});

socket.on('channel_watching_clear', () => {
    clearWatchingChannel();
});

socket.on('drop_progress', (data) => {
    updateDropProgress(data);
});

socket.on('drop_progress_stop', () => {
    clearDropProgress();
});

socket.on('campaign_add', (data) => {
    addCampaign(data);
});

socket.on('inventory_clear', () => {
    clearInventory();
});

socket.on('inventory_batch_update', (data) => {
    // Replace all campaigns atomically to prevent flickering
    state.campaigns = {};
    data.campaigns.forEach(camp => {
        state.campaigns[camp.id] = camp;
    });
    renderInventory();
});

socket.on('drop_update', (data) => {
    updateDrop(data.campaign_id, data.drop);
});

socket.on('login_required', () => {
    showLoginForm();
});

socket.on('oauth_code_required', (data) => {
    showOAuthCode(data.url, data.code);
});

socket.on('login_status', (data) => {
    updateLoginStatus(data);
});

socket.on('login_clear', (data) => {
    if (data.login) document.getElementById('username').value = '';
    if (data.password) document.getElementById('password').value = '';
    if (data.token) document.getElementById('2fa-token').value = '';
});

socket.on('settings_updated', (data) => {
    updateSettingsUI(data);
});

socket.on('games_available', (data) => {
    state.availableGames = data.games;
});

socket.on('theme_change', (data) => {
    applyDarkMode(data.dark_mode);
});

socket.on('notification', (data) => {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(data.title, {
            body: data.message,
            icon: '/static/icon.png'
        });
    }
});

socket.on('attention_required', (data) => {
    if (data.sound) {
        // Play notification sound
        const audio = new Audio('/static/notification.mp3');
        audio.play().catch(() => { });
    }
    // Flash title
    flashTitle();
});

socket.on('manual_mode_update', (data) => {
    updateManualModeUI(data);
});

socket.on('language_changed', (data) => {
    console.log('Language changed to:', data.language);
    fetchAndApplyTranslations();
});

socket.on('auto_watch_update', (data) => {
    state.autoWatchGames = data.games || [];
    renderAutoWatchList();
    // channels are filtered against the effective watch list
    renderChannels();
});

socket.on('wanted_items_update', (data) => {
    renderWantedItems(data);
});

socket.on('unlinked_auto_items_update', (data) => {
    renderUnlinkedAutoItems(data);
});

socket.on('drop_collected', (data) => {
    const toastText = state.translations.gui?.toasts || {};
    const benefits = (data.benefits || []).join(', ');
    showToast(
        'info',
        toastText.drop_collected_headline || 'Drop Collected',
        (toastText.drop_collected_message || '{game}: {benefits}')
            .replace('{game}', data.game)
            .replace('{benefits}', benefits)
    );
});

// ==================== UI Update Functions ====================

function updateStatus(status) {
    document.getElementById('status-text').textContent = status;

    // Note: the full-page loading overlay (see "Loading Overlay" section below)
    // is opt-in and only shown explicitly (e.g. around reloadCampaigns()) - it
    // is NOT tied to every status_update, since the backend uses batch updates
    // to keep the UI responsive during normal background operations.
}

function addConsoleLine(message) {
    addConsoleLineRaw(message);
}

function addConsoleLineRaw(line) {
    const console = document.getElementById('console-output');
    const div = document.createElement('div');
    div.textContent = line;
    console.appendChild(div);
    // Auto-scroll to bottom
    console.scrollTop = console.scrollHeight;
    // Limit lines
    while (console.children.length > 1000) {
        console.removeChild(console.firstChild);
    }
}

function updateChannel(channelData) {
    state.channels[channelData.id] = channelData;
    renderChannels();
}

function removeChannel(channelId) {
    delete state.channels[channelId];
    renderChannels();
}

function clearChannels() {
    state.channels = {};
    renderChannels();
}

function setWatchingChannel(channelId) {
    Object.values(state.channels).forEach(ch => ch.watching = false);
    if (state.channels[channelId]) {
        state.channels[channelId].watching = true;
    }
    renderChannels();
}

function clearWatchingChannel() {
    Object.values(state.channels).forEach(ch => ch.watching = false);
    renderChannels();
}

function renderChannels() {
    const container = document.getElementById('channels-list');
    container.innerHTML = '';

    const t = state.translations;
    const channels = Object.values(state.channels);
    if (channels.length === 0) {
        const emptyMsg = t.gui?.channels?.no_channels || 'No channels tracked yet...';
        container.replaceChildren(
            makeElement('p', { class: 'empty-message' }, emptyMsg),
        );
        return;
    }

    // Get the effective watch list: user picks + library-detected games
    const gamesToWatch = (state.settings.games_to_watch || []).concat(state.autoWatchGames || []);
    const gamesToWatchSet = new Set(gamesToWatch);

    // Filter channels to only include those playing games in the watch list
    const filteredChannels = channels.filter(channel => {
        const gameName = channel.game;
        // Include channels if: they have a game AND it's in the watch list
        // OR if the watch list is empty (show all)
        return gamesToWatch.length === 0 || (gameName && gamesToWatchSet.has(gameName));
    });

    if (filteredChannels.length === 0) {
        const emptyMsg = t.gui?.channels?.no_channels_for_games || 'No channels found for selected games...';
        const emptySubMsg = t.gui?.channels?.no_channels_for_games_sub || '(Idle mode will not list channels here)';
        container.replaceChildren(
            makeElement('p', { class: 'empty-message' }, '', el => {
                el.appendChild(document.createTextNode(emptyMsg));
                el.appendChild(document.createElement('br'));
                el.appendChild(makeElement('span', { class: 'sub-message' }, emptySubMsg));
            }),
        );
        return;
    }

    // Group channels by game
    const gameGroups = {};
    filteredChannels.forEach(channel => {
        const gameName = channel.game || 'No Game';
        const gameId = channel.game_id || 'no-game';
        const gameIcon = channel.game_icon;

        if (!gameGroups[gameId]) {
            gameGroups[gameId] = {
                name: gameName,
                icon: gameIcon,
                channels: []
            };
        }
        gameGroups[gameId].channels.push(channel);
    });

    // Sort games: prioritize games with watching channels, then by total viewers
    const sortedGames = Object.entries(gameGroups).sort(([idA, groupA], [idB, groupB]) => {
        const hasWatchingA = groupA.channels.some(ch => ch.watching);
        const hasWatchingB = groupB.channels.some(ch => ch.watching);

        if (hasWatchingA !== hasWatchingB) return hasWatchingB ? 1 : -1;

        // Sum total viewers for each game
        const totalViewersA = groupA.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);
        const totalViewersB = groupB.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);

        return totalViewersB - totalViewersA;
    });

    // Render each game group
    sortedGames.forEach(([gameId, group]) => {
        // Create game header
        const gameHeader = document.createElement('div');
        gameHeader.className = 'game-group-header';

        const channelCount = group.channels.length;
        const totalViewers = group.channels.reduce((sum, ch) => sum + (ch.viewers || 0), 0);

        const channelText = channelCount === 1
            ? (t.gui?.channels?.channel_count || 'channel')
            : (t.gui?.channels?.channel_count_plural || 'channels');
        const viewersText = t.gui?.channels?.viewers || 'viewers';

        if (group.icon) {
            gameHeader.appendChild(makeImageElement(group.icon.replace('{width}', '40').replace('{height}', '53'), group.name, 'game-icon'));
        }
        gameHeader.appendChild(makeElement('div', { class: 'game-group-info' }, null, el => {
            el.appendChild(makeElement('div', { class: 'game-group-name' }, group.name));
            el.appendChild(makeElement('div', { class: 'game-group-stats' }, `${channelCount} ${channelText} • ${totalViewers.toLocaleString()} ${viewersText}`));
        }));

        container.appendChild(gameHeader);

        // Sort channels within game: watching first, then online, then by viewers
        group.channels.sort((a, b) => {
            if (a.watching !== b.watching) return b.watching ? 1 : -1;
            if (a.online !== b.online) return b.online ? 1 : -1;
            return (b.viewers || 0) - (a.viewers || 0);
        });

        // Render channels in this game
        group.channels.forEach(channel => {
            const div = document.createElement('div');
            div.className = 'channel-item';
            if (channel.watching) div.classList.add('watching');
            if (channel.online) div.classList.add('online');
            else div.classList.add('offline');

            const nameDiv = makeElement('div', { class: 'channel-name' }, channel.name, el => {
                if (channel.drops_enabled) {
                    el.appendChild(document.createTextNode(' '));
                    el.appendChild(makeElement('span', { class: 'channel-badge drops' }, 'DROPS'));
                }
                if (channel.acl_based) {
                    el.appendChild(document.createTextNode(' '));
                    el.appendChild(makeElement('span', { class: 'channel-badge acl' }, 'ACL'));
                }
            });
            const infoDiv = makeElement('div', { class: 'channel-info' }, channel.viewers !== null ? channel.viewers.toLocaleString() + ' viewers' : 'Offline', el => {
                if (channel.watching) {
                    el.appendChild(document.createTextNode(' • '));
                    el.appendChild(makeElement('strong', {}, 'WATCHING'));
                }
            });
            div.replaceChildren(nameDiv, infoDiv);

            div.onclick = () => selectChannel(channel.id);
            container.appendChild(div);
        });
    });
}

function updateDropProgress(data) {
    // Check if this is a new drop or if remaining seconds changed significantly
    const isNewDrop = !state.currentDrop || state.currentDrop.drop_id !== data.drop_id;

    // Store old remaining seconds before updating state
    const oldRemaining = state.currentDrop ? state.currentDrop.remaining_seconds : null;

    // Update state with new data
    state.currentDrop = data;

    document.getElementById('no-drop-message').style.display = 'none';
    document.getElementById('drop-info').style.display = 'block';

    document.getElementById('drop-name').textContent = data.drop_name;

    // Make campaign name clickable with link to Twitch
    const dropGameEl = document.getElementById('drop-game');
    if (data.campaign_id) {
        const campaignUrl = `https://www.twitch.tv/drops/campaigns?dropID=${data.campaign_id}`;
        dropGameEl.replaceChildren(
            makeElement('a', { href: campaignUrl, target: '_blank', rel: 'noopener noreferrer', class: 'drop-campaign-link' }, data.campaign_name),
            document.createTextNode(` (${data.game_name})`),
        );
    } else {
        dropGameEl.textContent = `${data.campaign_name} (${data.game_name})`;
    }

    const progress = data.progress * 100;
    const fill = document.getElementById('progress-fill');
    fill.style.width = `${progress}%`;
    fill.textContent = `${Math.round(progress)}%`;

    document.getElementById('progress-text').textContent =
        `${data.current_minutes} / ${data.required_minutes} minutes`;

    // Warn when the shown minutes include a local estimate (Twitch isn't reporting progress)
    const estimatedEl = document.getElementById('progress-estimated');
    if (data.is_estimated) {
        const progressT = state.translations.gui?.progress || {};
        estimatedEl.textContent = `⚠ ${progressT.estimated_badge || 'estimated'}`;
        estimatedEl.title = progressT.estimated_tooltip ||
            'Twitch is not reporting progress right now - the shown minutes include a local estimate and may be inaccurate.';
        estimatedEl.style.display = 'block';
    } else {
        estimatedEl.style.display = 'none';
    }

    // Only reset the timer if it's a new drop or if backend time differs by more than 2 seconds
    // This prevents constant timer resets from periodic backend updates
    const shouldResetTimer = isNewDrop || oldRemaining === null || Math.abs(oldRemaining - data.remaining_seconds) > 2;

    if (shouldResetTimer) {
        // Cancel any existing countdown timer before starting a new one
        if (state.countdownTimer !== null) {
            clearTimeout(state.countdownTimer);
            state.countdownTimer = null;
        }

        // Start countdown with the new value from backend
        updateRemainingTime(data.remaining_seconds);
    }
    // Otherwise, let the existing timer continue counting down smoothly
}

function updateRemainingTime(seconds) {
    const timeEl = document.getElementById('progress-time');

    // A local estimate can overshoot the required minutes before Twitch confirms
    // progress, driving the ETA negative - show a pending message instead of that.
    if (seconds < 0) {
        const progressT = state.translations.gui?.progress || {};
        timeEl.textContent = progressT.confirmation_pending || 'Drop confirmation pending...';
        state.countdownTimer = null;
        return;
    }

    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    timeEl.textContent = `Time remaining: ${minutes}:${secs.toString().padStart(2, '0')}`;

    if (seconds > 0) {
        // Store the timer ID so we can cancel it if needed
        state.countdownTimer = setTimeout(() => updateRemainingTime(seconds - 1), 1000);
    } else {
        state.countdownTimer = null;
    }
}

function clearDropProgress() {
    state.currentDrop = null;

    // Cancel any active countdown timer
    if (state.countdownTimer !== null) {
        clearTimeout(state.countdownTimer);
        state.countdownTimer = null;
    }

    document.getElementById('no-drop-message').style.display = 'block';
    document.getElementById('drop-info').style.display = 'none';
}

function addCampaign(campaignData) {
    state.campaigns[campaignData.id] = campaignData;
    renderInventory();
}

function clearInventory() {
    state.campaigns = {};
    state.linkClickedCampaigns.clear();
    renderInventory();
}

function updateDrop(campaignId, dropData) {
    if (state.campaigns[campaignId]) {
        const drops = state.campaigns[campaignId].drops;
        const index = drops.findIndex(d => d.id === dropData.id);
        if (index !== -1) {
            drops[index] = dropData;
            renderInventory();
        }
    }
}

// ==================== Inventory Filtering ====================

function getInventoryFilters() {
    // Get filter state from UI checkboxes and selected games array
    return {
        show_active: document.getElementById('filter-active')?.checked || false,
        show_not_linked: document.getElementById('filter-not-linked')?.checked || false,
        show_upcoming: document.getElementById('filter-upcoming')?.checked || false,
        show_expired: document.getElementById('filter-expired')?.checked || false,
        show_finished: document.getElementById('filter-finished')?.checked || false,
        game_name_search: [...selectedInventoryGames],  // Array of selected game names
        // Benefit type filters (default to true if checkbox doesn't exist)
        show_benefit_item: document.getElementById('filter-benefit-item')?.checked !== false,
        show_benefit_badge: document.getElementById('filter-benefit-badge')?.checked !== false,
        show_benefit_emote: document.getElementById('filter-benefit-emote')?.checked !== false,
        show_benefit_other: document.getElementById('filter-benefit-other')?.checked !== false
    };
}


// Filtering now happens per DROP (matching the bucket it lands in), not per whole
// campaign - otherwise unchecking e.g. "Collected" wouldn't hide already-claimed drops
// that belong to a campaign which is still "Active" overall.
function dropMatchesStatusFilter(bucket, filters) {
    const anyStatusFilter = filters.show_active || filters.show_not_linked ||
        filters.show_upcoming || filters.show_expired || filters.show_finished;
    if (!anyStatusFilter) return true;
    return !!filters[`show_${bucket}`];
}

function dropMatchesBenefitFilter(drop, filters) {
    const allBenefitsEnabled = filters.show_benefit_item && filters.show_benefit_badge &&
        filters.show_benefit_emote && filters.show_benefit_other;
    if (allBenefitsEnabled) return true;
    if (!drop.benefits || drop.benefits.length === 0) return false;
    return drop.benefits.some(benefit => {
        const benefitType = (benefit.type || '').toUpperCase();
        if (filters.show_benefit_item && benefitType === 'DIRECT_ENTITLEMENT') return true;
        if (filters.show_benefit_badge && benefitType === 'BADGE') return true;
        if (filters.show_benefit_emote && benefitType === 'EMOTE') return true;
        if (filters.show_benefit_other && benefitType === 'UNKNOWN') return true;
        return false;
    });
}

function campaignMatchesGameFilter(campaign, filters) {
    if (!filters.game_name_search || filters.game_name_search.length === 0) return true;
    return filters.game_name_search.includes(campaign.game_name);
}


function onInventoryFilterChange() {
    // Save filter state to settings and re-render inventory
    saveSettings();
    renderInventory();
}

// Smart-expand: checking a status filter forces its section open once (never auto-collapses
// another). Only wired to the 5 status checkboxes, not every render, so a section the user
// manually collapses afterwards stays collapsed instead of snapping back open.
function applyStatusFilterSmartExpand() {
    const filters = getInventoryFilters();
    INVENTORY_SECTION_ORDER.forEach(key => {
        if (filters[`show_${key}`]) {
            state.inventorySections[key] = true;
        }
    });
}

function onInventoryStatusFilterChange() {
    applyStatusFilterSmartExpand();
    onInventoryFilterChange();
}

function clearInventoryFilters() {
    // Reset status filters to the standard default view (Active + Not Linked + Upcoming
    // shown, Expired/Collected hidden) - not the Categories toggle, which is untouched.
    document.getElementById('filter-active').checked = true;
    document.getElementById('filter-not-linked').checked = true;
    document.getElementById('filter-upcoming').checked = true;
    document.getElementById('filter-expired').checked = false;
    document.getElementById('filter-finished').checked = false;
    document.getElementById('inventory-game-search').value = '';

    // Reset benefit type filters to checked (show all)
    if (document.getElementById('filter-benefit-item')) document.getElementById('filter-benefit-item').checked = true;
    if (document.getElementById('filter-benefit-badge')) document.getElementById('filter-benefit-badge').checked = true;
    if (document.getElementById('filter-benefit-emote')) document.getElementById('filter-benefit-emote').checked = true;
    if (document.getElementById('filter-benefit-other')) document.getElementById('filter-benefit-other').checked = true;

    // Clear selected games
    selectedInventoryGames = [];
    updateGameTagsDisplay();

    // Save and re-render
    applyStatusFilterSmartExpand();
    saveSettings();
    renderInventory();
}


// ==================== Game Dropdown & Tags ====================

// Track selected games for inventory filter
let selectedInventoryGames = [];
let gameDropdownFocusedIndex = -1;
let gameDropdownVisible = false;

function getAvailableGamesForDropdown() {
    // Combine games from campaigns and availableGames Set
    const gamesFromCampaigns = Object.values(state.campaigns).map(c => c.game_name);
    const gamesFromSettings = Array.from(availableGames || []);

    // Merge and deduplicate
    const allGames = [...new Set([...gamesFromCampaigns, ...gamesFromSettings])];

    // Sort alphabetically
    return allGames.sort((a, b) => a.localeCompare(b));
}

function renderGameDropdown(searchTerm = '') {
    const dropdown = document.getElementById('game-dropdown-list');
    const allGames = getAvailableGamesForDropdown();

    // Filter games by search term (case-insensitive)
    const searchLower = searchTerm.toLowerCase().trim();
    const filteredGames = searchLower
        ? allGames.filter(game => game.toLowerCase().includes(searchLower))
        : allGames;

    dropdown.innerHTML = '';

    if (filteredGames.length === 0) {
        dropdown.replaceChildren(makeElement('div', { class: 'dropdown-item no-results' }, 'No games found'));
        gameDropdownFocusedIndex = -1;
        return;
    }

    filteredGames.forEach((gameName, index) => {
        const isSelected = selectedInventoryGames.includes(gameName);
        const isFocused = index === gameDropdownFocusedIndex;

        const item = document.createElement('div');
        item.className = 'dropdown-item' + (isFocused ? ' focused' : '');
        item.dataset.gameName = gameName;
        item.dataset.index = index;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isSelected;
        checkbox.id = `game-dropdown-${index}`;

        const label = document.createElement('label');
        label.setAttribute('for', `game-dropdown-${index}`);
        label.textContent = gameName;

        item.appendChild(checkbox);
        item.appendChild(label);

        // Click handler for the entire item
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleGameSelection(gameName);
        });

        dropdown.appendChild(item);
    });
}

function toggleGameSelection(gameName) {
    const index = selectedInventoryGames.indexOf(gameName);
    if (index >= 0) {
        // Remove game
        selectedInventoryGames.splice(index, 1);
    } else {
        // Add game
        selectedInventoryGames.push(gameName);
    }

    updateGameTagsDisplay();
    renderGameDropdown(document.getElementById('inventory-game-search').value);
    saveSettings();
    renderInventory();
}

function removeGameTag(gameName) {
    const index = selectedInventoryGames.indexOf(gameName);
    if (index >= 0) {
        selectedInventoryGames.splice(index, 1);
        updateGameTagsDisplay();
        renderGameDropdown(document.getElementById('inventory-game-search').value);
        saveSettings();
        renderInventory();
    }
}

function updateGameTagsDisplay() {
    const container = document.getElementById('selected-game-tags');
    container.innerHTML = '';

    selectedInventoryGames.forEach(gameName => {
        const tag = document.createElement('div');
        tag.className = 'game-tag';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'game-tag-name';
        nameSpan.textContent = gameName;

        const removeBtn = document.createElement('button');
        removeBtn.className = 'game-tag-remove';
        removeBtn.textContent = '×';
        removeBtn.setAttribute('aria-label', `Remove ${gameName}`);
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeGameTag(gameName);
        });

        tag.appendChild(nameSpan);
        tag.appendChild(removeBtn);
        container.appendChild(tag);
    });
}

function showGameDropdown() {
    const dropdown = document.getElementById('game-dropdown-list');
    dropdown.style.display = 'block';
    gameDropdownVisible = true;
    gameDropdownFocusedIndex = -1;
    renderGameDropdown(document.getElementById('inventory-game-search').value);
}

function closeGameDropdown() {
    const dropdown = document.getElementById('game-dropdown-list');
    dropdown.style.display = 'none';
    gameDropdownVisible = false;
    gameDropdownFocusedIndex = -1;
}

function handleGameSearchKeydown(event) {
    if (!gameDropdownVisible) {
        return;
    }

    const dropdown = document.getElementById('game-dropdown-list');
    const items = dropdown.querySelectorAll('.dropdown-item:not(.no-results)');
    const maxIndex = items.length - 1;

    if (event.key === 'ArrowDown') {
        event.preventDefault();
        gameDropdownFocusedIndex = Math.min(gameDropdownFocusedIndex + 1, maxIndex);
        renderGameDropdown(document.getElementById('inventory-game-search').value);

        // Scroll focused item into view
        const focusedItem = dropdown.querySelector('.dropdown-item.focused');
        if (focusedItem) {
            focusedItem.scrollIntoView({ block: 'nearest' });
        }
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        gameDropdownFocusedIndex = Math.max(gameDropdownFocusedIndex - 1, 0);
        renderGameDropdown(document.getElementById('inventory-game-search').value);

        // Scroll focused item into view
        const focusedItem = dropdown.querySelector('.dropdown-item.focused');
        if (focusedItem) {
            focusedItem.scrollIntoView({ block: 'nearest' });
        }
    } else if (event.key === 'Enter') {
        event.preventDefault();
        if (gameDropdownFocusedIndex >= 0 && gameDropdownFocusedIndex <= maxIndex) {
            const focusedItem = items[gameDropdownFocusedIndex];
            const gameName = focusedItem.dataset.gameName;
            if (gameName) {
                toggleGameSelection(gameName);
            }
        }
    } else if (event.key === 'Escape') {
        event.preventDefault();
        closeGameDropdown();
        document.getElementById('inventory-game-search').blur();
    }
}

// ==================== Inventory Tree (Game > Campaign > Drop) ====================

// Section order top-to-bottom; default expand state lives in state.inventorySections.
const INVENTORY_SECTION_ORDER = ['active', 'not_linked', 'upcoming', 'finished', 'expired'];

function isCampaignFinished(campaign) {
    return campaign.total_drops > 0 && campaign.claimed_drops === campaign.total_drops;
}

// Groups already-filtered { campaign, drops } entries by game name, sorting games
// alphabetically and campaigns within a game by start date.
function groupEntriesByGame(entries) {
    const byGame = {};
    entries.forEach(entry => {
        const key = entry.campaign.game_name;
        if (!byGame[key]) {
            byGame[key] = { game_name: key, game_box_art_url: entry.campaign.game_box_art_url, campaigns: [] };
        }
        byGame[key].campaigns.push(entry);
    });
    const games = Object.values(byGame);
    games.forEach(game => game.campaigns.sort((a, b) => new Date(a.campaign.starts_at) - new Date(b.campaign.starts_at)));
    games.sort((a, b) => a.game_name.localeCompare(b.game_name));
    return games;
}

// Bucketing happens per DROP, not per campaign: a single campaign's drops can land in
// different sections at once (one already claimed, another still counting down, another
// not unlocked yet), so the same game/campaign can appear under several sections
// simultaneously - each instance listing only the drops that belong there.
function inventoryBucketForDrop(drop, campaign, now) {
    if (drop.is_claimed) return 'finished';
    const startsAt = new Date(drop.starts_at).getTime();
    const endsAt = new Date(drop.ends_at).getTime();
    const inActiveWindow = now >= startsAt && now <= endsAt;
    const inUpcomingWindow = now < startsAt;
    if (!campaign.linked && (inActiveWindow || inUpcomingWindow)) return 'not_linked';
    if (inActiveWindow) return 'active';
    if (inUpcomingWindow) return 'upcoming';
    return 'expired';
}

// Categories-off view: pools every bucket's matching drops back into one entry per
// campaign (instead of splitting them across sections), still filtered per-drop by the
// current status/benefit filters.
function collectFilteredEntries(campaigns, filters) {
    const now = Date.now();
    const byCampaignId = {};
    const order = [];
    campaigns.forEach(campaign => {
        if (!campaignMatchesGameFilter(campaign, filters)) return;
        campaign.drops.forEach(drop => {
            const bucket = inventoryBucketForDrop(drop, campaign, now);
            if (!dropMatchesStatusFilter(bucket, filters)) return;
            if (!dropMatchesBenefitFilter(drop, filters)) return;
            let entry = byCampaignId[campaign.id];
            if (!entry) {
                entry = { campaign, drops: [] };
                byCampaignId[campaign.id] = entry;
                order.push(entry);
            }
            entry.drops.push(drop);
        });
    });
    return order;
}

// Returns { [sectionKey]: [{ game_name, game_box_art_url, campaigns: [{ campaign, drops }] }] }
// - "campaigns" here only carries the drops relevant to both that section's bucket and
// the current status/benefit/game filters.
function buildInventoryTree(campaigns, filters) {
    const now = Date.now();
    const bucketEntries = { active: [], not_linked: [], upcoming: [], finished: [], expired: [] };
    const seenByBucket = { active: {}, not_linked: {}, upcoming: {}, finished: {}, expired: {} };

    campaigns.forEach(campaign => {
        if (!campaignMatchesGameFilter(campaign, filters)) return;
        campaign.drops.forEach(drop => {
            const bucket = inventoryBucketForDrop(drop, campaign, now);
            if (!dropMatchesStatusFilter(bucket, filters)) return;
            if (!dropMatchesBenefitFilter(drop, filters)) return;

            const seen = seenByBucket[bucket];
            let entry = seen[campaign.id];
            if (!entry) {
                entry = { campaign, drops: [] };
                seen[campaign.id] = entry;
                bucketEntries[bucket].push(entry);
            }
            entry.drops.push(drop);
        });
    });

    const tree = {};
    INVENTORY_SECTION_ORDER.forEach(key => {
        tree[key] = groupEntriesByGame(bucketEntries[key]);
    });
    return tree;
}

function formatCampaignDateRange(campaign, t) {
    const start = campaign.starts_at ? new Date(campaign.starts_at).toLocaleString() : null;
    const end = campaign.ends_at ? new Date(campaign.ends_at).toLocaleString() : null;
    if (start && end) return `${start} – ${end}`;
    if (start) return (t.gui?.inventory?.starts || 'Starts: {time}').replace('{time}', start);
    if (end) return (t.gui?.inventory?.ends || 'Ends: {time}').replace('{time}', end);
    return '';
}

function buildInventoryDropRow(drop, campaign, t) {
    const benefits = drop.benefits || [];
    const firstBenefit = benefits[0] || null;
    const extraCount = benefits.length - 1;
    const isExpired = inventoryBucketForDrop(drop, campaign, Date.now()) === 'expired';

    const rowClass = [
        'inventory-drop-row',
        drop.is_claimed ? 'claimed' : '',
        drop.can_claim ? 'active' : '',
        isExpired ? 'uncollectible' : ''
    ].filter(Boolean).join(' ');

    return makeElement('div', { class: rowClass }, '', row => {
        if (firstBenefit) {
            row.appendChild(makeImageElement(firstBenefit.image_url, firstBenefit.name, 'benefit-icon inventory-drop-icon'));
        }
        row.appendChild(makeElement('span', { class: 'inventory-drop-name', title: drop.name }, drop.name));
        if (firstBenefit) {
            const benefitText = `${firstBenefit.name} (${firstBenefit.type})`;
            const extraNames = extraCount > 0 ? benefits.slice(1).map(b => `${b.name} (${b.type})`).join(', ') : '';
            row.appendChild(makeElement('span', {
                class: 'inventory-drop-benefit',
                title: extraCount > 0 ? `${benefitText}; ${extraNames}` : benefitText
            }, extraCount > 0 ? `${benefitText} +${extraCount}` : benefitText));
        }
        row.appendChild(makeElement('span', { class: `inventory-drop-progress${drop.is_claimed ? ' claimed' : ''}` },
            `${drop.current_minutes} / ${drop.required_minutes} min (${Math.round(drop.progress * 100)}%)`));
    });
}

// Shared by both campaign-row variants below: link badge, name link, date range,
// claimed/total progress, and the Link Account / Refresh Status buttons.
function appendCampaignRowCommonParts(el, campaign, t) {
    const claimedCountText = t.gui?.inventory?.claimed_drops || 'claimed';

    el.appendChild(makeElement('a', {
        href: campaign.campaign_url, target: '_blank', rel: 'noopener noreferrer',
        class: 'campaign-name-link inventory-campaign-name', title: campaign.name
    }, campaign.name, link =>
        link.appendChild(makeElement('span', { class: 'external-link-icon' }, '🔗'))
    ));

    const dateRange = formatCampaignDateRange(campaign, t);
    if (dateRange) {
        el.appendChild(makeElement('span', { class: 'inventory-campaign-dates', title: dateRange }, dateRange));
    }

    el.appendChild(makeElement('span', { class: 'inventory-campaign-progress' },
        `${campaign.claimed_drops} / ${campaign.total_drops} ${claimedCountText}`));

    if (!campaign.linked && campaign.link_url) {
        el.appendChild(makeElement('button', { class: 'link-account-btn inventory-link-btn' }, 'Link Account', btn => {
            btn.addEventListener('click', () => {
                window.open(campaign.link_url, '_blank');
                if (!state.linkClickedCampaigns.has(campaign.id)) {
                    state.linkClickedCampaigns.add(campaign.id);
                    renderInventory();
                }
            });
        }));
        if (state.linkClickedCampaigns.has(campaign.id)) {
            const refreshStatusText = t.gui?.inventory?.refresh_status || 'Refresh Status';
            el.appendChild(makeElement('button', { class: 'link-account-btn refresh-status-btn inventory-link-btn' }, refreshStatusText, btn => {
                btn.addEventListener('click', () => reloadCampaigns({
                    kind: 'campaign',
                    campaignId: campaign.id,
                    gameName: campaign.game_name
                }));
            }));
        }
    }
}

// Categories-off view: still filtered per-drop (see collectFilteredEntries), just pooled
// into one campaign instance instead of split across sections. Status badge is computed
// from the campaign's own active/upcoming/expired/finished state (there's no single
// section context here to derive it from).
function buildFlatCampaignGroup(entry, t) {
    const { campaign, drops } = entry;
    let statusClass = 'expired';
    let statusText = t.gui?.inventory?.status?.expired || 'Expired';
    if (!campaign.linked && (campaign.active || campaign.upcoming)) {
        // Matches inventoryBucketForDrop's priority: an unlinked campaign's drops are always
        // bucketed as "not_linked" while in their window, never "active" - so the aggregate
        // badge here must agree, or it would falsely claim "Linked" for an unlinked campaign.
        statusClass = 'not_linked';
        statusText = t.gui?.inventory?.filters?.not_linked || 'Not Linked';
    } else if (campaign.active) {
        statusClass = 'active';
        statusText = t.gui?.inventory?.status?.active || 'Linked';
    } else if (campaign.upcoming) {
        statusClass = 'upcoming';
        statusText = t.gui?.inventory?.status?.upcoming || 'Upcoming';
    } else if (isCampaignFinished(campaign)) {
        statusClass = 'finished';
        statusText = t.gui?.inventory?.status?.finished || 'Collected';
    }

    const campaignRow = makeElement('div', { class: 'inventory-campaign-row' }, '', el => {
        el.appendChild(makeElement('span', { class: `inventory-status-badge ${statusClass}` }, statusText));
        appendCampaignRowCommonParts(el, campaign, t);
    });

    const dropsContainer = makeElement('div', { class: 'inventory-drops' });
    drops.forEach(drop => dropsContainer.appendChild(buildInventoryDropRow(drop, campaign, t)));

    return makeElement('div', { class: 'inventory-campaign-group' }, '', el => {
        el.appendChild(campaignRow);
        el.appendChild(dropsContainer);
    });
}

// Sectioned view: one campaign instance per section it has drops in, showing only
// that section's subset of drops. Status badge reflects the section itself (skipped
// for "not_linked" since the account-link badge already says as much).
function buildSectionCampaignGroup(entry, sectionKey, t) {
    const { campaign, drops } = entry;
    const sectionLabel = t.gui?.inventory?.filters?.[sectionKey] || sectionKey;

    const campaignRow = makeElement('div', { class: 'inventory-campaign-row' }, '', el => {
        if (sectionKey !== 'not_linked') {
            el.appendChild(makeElement('span', { class: `inventory-status-badge ${sectionKey}` }, sectionLabel));
        }
        appendCampaignRowCommonParts(el, campaign, t);
    });

    const dropsContainer = makeElement('div', { class: 'inventory-drops' });
    drops.forEach(drop => dropsContainer.appendChild(buildInventoryDropRow(drop, campaign, t)));

    return makeElement('div', { class: 'inventory-campaign-group' }, '', el => {
        el.appendChild(campaignRow);
        el.appendChild(dropsContainer);
    });
}

function buildInventoryGameRow(game, t) {
    const countText = game.campaigns.length === 1
        ? (t.gui?.inventory?.campaign_count || 'campaign')
        : (t.gui?.inventory?.campaign_count_plural || 'campaigns');

    return makeElement('div', { class: 'inventory-game-row' }, '', el => {
        if (game.game_box_art_url) {
            const iconUrl = game.game_box_art_url.replace('{width}', '52').replace('{height}', '70');
            el.appendChild(makeImageElement(iconUrl, game.game_name, 'game-icon inventory-game-icon'));
        }
        el.appendChild(makeElement('span', { class: 'inventory-game-name', title: game.game_name }, game.game_name));
        el.appendChild(makeElement('span', { class: 'inventory-game-count' }, `${game.campaigns.length} ${countText}`));
    });
}

function buildFlatGameGroup(game, t) {
    return makeElement('div', { class: 'inventory-game-group' }, '', group => {
        group.appendChild(buildInventoryGameRow(game, t));
        const campaignsContainer = makeElement('div', { class: 'inventory-campaigns' });
        game.campaigns.forEach(entry => campaignsContainer.appendChild(buildFlatCampaignGroup(entry, t)));
        group.appendChild(campaignsContainer);
    });
}

function buildSectionGameGroup(game, sectionKey, t) {
    return makeElement('div', { class: 'inventory-game-group' }, '', group => {
        group.appendChild(buildInventoryGameRow(game, t));
        const campaignsContainer = makeElement('div', { class: 'inventory-campaigns' });
        game.campaigns.forEach(entry => campaignsContainer.appendChild(buildSectionCampaignGroup(entry, sectionKey, t)));
        group.appendChild(campaignsContainer);
    });
}

// Toggles the collapsed class directly on the already-built section element instead of
// triggering a full renderInventory() - a full rebuild would recreate every row in every
// section (images included), replaying entrance animations and causing a visible flash
// just to collapse/expand one section whose content hasn't actually changed.
function toggleInventorySection(key, sectionEl) {
    const expanded = !state.inventorySections[key];
    state.inventorySections[key] = expanded;
    if (sectionEl) sectionEl.classList.toggle('collapsed', !expanded);
    saveSettings();
}

function buildInventorySection(key, games, t) {
    const label = t.gui?.inventory?.filters?.[key] || key;
    const expanded = !!state.inventorySections[key];
    const dropCount = games.reduce((sum, game) =>
        sum + game.campaigns.reduce((s, entry) => s + entry.drops.length, 0), 0);

    return makeElement('section', { class: `inventory-section${expanded ? '' : ' collapsed'}` }, '', section => {
        section.appendChild(makeElement('div', { class: 'inventory-section-header' }, '', header => {
            header.appendChild(makeElement('span', { class: 'inventory-section-chevron' }, '▸'));
            header.appendChild(makeElement('span', { class: 'inventory-section-title' }, label));
            header.appendChild(makeElement('span', { class: 'inventory-section-count' }, `(${dropCount})`));
            header.addEventListener('click', () => toggleInventorySection(key, section));
        }));

        const body = makeElement('div', { class: 'inventory-section-body' });
        games.forEach(game => body.appendChild(buildSectionGameGroup(game, key, t)));
        section.appendChild(body);
    });
}

function renderInventory() {
    const container = document.getElementById('inventory-grid');
    const t = state.translations;
    const allCampaigns = Object.values(state.campaigns);

    if (allCampaigns.length === 0) {
        const emptyMsg = t.gui?.inventory?.no_campaigns || 'No campaigns loaded yet...';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    const filters = getInventoryFilters();
    const noMatchesMsg = t.gui?.inventory?.no_matches || 'No campaigns match the current filters.';

    const categoriesEnabled = getInventoryViewModeFromUI() === 'category';
    const fragment = document.createDocumentFragment();

    if (categoriesEnabled) {
        const tree = buildInventoryTree(allCampaigns, filters);
        let anySection = false;
        INVENTORY_SECTION_ORDER.forEach(key => {
            const games = tree[key];
            if (games.length === 0) return;
            anySection = true;
            fragment.appendChild(buildInventorySection(key, games, t));
        });
        if (!anySection) {
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, noMatchesMsg));
            return;
        }
    } else {
        const games = groupEntriesByGame(collectFilteredEntries(allCampaigns, filters));
        if (games.length === 0) {
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, noMatchesMsg));
            return;
        }
        const flatList = makeElement('div', { class: 'inventory-flat-list' });
        games.forEach(game => flatList.appendChild(buildFlatGameGroup(game, t)));
        fragment.appendChild(flatList);
    }

    container.replaceChildren(fragment);
}

function showLoginForm() {
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('oauth-code-display').style.display = 'none';
}

function showOAuthCode(url, code) {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('oauth-code-display').style.display = 'block';
    document.getElementById('oauth-url').href = url;
    document.getElementById('oauth-code').textContent = code;
}

function updateLoginStatus(data) {
    const statusEl = document.getElementById('login-status');
    const t = state.translations;
    if (data.user_id) {
        const userIdLabel = t.gui?.login?.user_id_label || 'User ID:';
        statusEl.textContent = `${data.status} (${userIdLabel} ${data.user_id})`;
        statusEl.removeAttribute('translation-key');
        statusEl.style.color = 'var(--success-color)';
        document.getElementById('login-form').style.display = 'none';
        document.getElementById('oauth-code-display').style.display = 'none';
    } else {
        const loggedOut = t.gui?.login?.logged_out || 'Not logged in';
        statusEl.textContent = data.status || loggedOut;
        statusEl.setAttribute('translation-key', 'logged_out');
        statusEl.style.color = 'var(--text-secondary)';
        // Check if OAuth is pending (for late-connecting clients)
        if (data.oauth_pending) {
            showOAuthCode(data.oauth_pending.url, data.oauth_pending.code);
        }
    }
}

function updateSettingsUI(settings) {
    state.settings = settings;
    setDarkModeUI(settings.dark_mode);
    setAnimationsModeUI(settings.animations);
    const connectionQualityInput = document.getElementById('connection-quality');
    connectionQualityInput.value = settings.connection_quality || 1;
    updateSliderVisual(connectionQualityInput);
    const refreshIntervalInput = document.getElementById('minimum-refresh-interval');
    refreshIntervalInput.value = settings.minimum_refresh_interval_minutes || 30;
    updateSliderVisual(refreshIntervalInput);

    // Update proxy settings and indicator
    const proxyUrl = settings.proxy || '';
    const proxyInput = document.getElementById('proxy-url');
    if (proxyInput) proxyInput.value = proxyUrl;

    const proxyIndicator = document.getElementById('proxy-indicator');
    if (proxyIndicator) {
        proxyIndicator.style.display = proxyUrl ? 'inline-flex' : 'none';
        proxyIndicator.title = proxyUrl ? `Proxy active: ${proxyUrl}` : 'Proxy disabled';
    }

    // Update language dropdown if we have the current language
    if (settings.language) {
        const languageSelect = document.getElementById('language');
        if (languageSelect) {
            languageSelect.value = settings.language;
        }
    }

    // Update available games if provided in settings
    if (settings.games_available) {
        availableGames = new Set(settings.games_available);
    }

    // Restore inventory filters from settings
    if (settings.inventory_filters) {
        document.getElementById('filter-active').checked = settings.inventory_filters.show_active || false;
        document.getElementById('filter-not-linked').checked = settings.inventory_filters.show_not_linked || false;
        document.getElementById('filter-upcoming').checked = settings.inventory_filters.show_upcoming || false;
        document.getElementById('filter-expired').checked = settings.inventory_filters.show_expired || false;
        document.getElementById('filter-finished').checked = settings.inventory_filters.show_finished || false;

        // Restore selected games array
        selectedInventoryGames = Array.isArray(settings.inventory_filters.game_name_search)
            ? [...settings.inventory_filters.game_name_search]
            : [];  // Handle old string format gracefully
        updateGameTagsDisplay();

        // Restore benefit type filters (default to true if not set)
        if (document.getElementById('filter-benefit-item')) document.getElementById('filter-benefit-item').checked = settings.inventory_filters.show_benefit_item !== false;
        if (document.getElementById('filter-benefit-badge')) document.getElementById('filter-benefit-badge').checked = settings.inventory_filters.show_benefit_badge !== false;
        if (document.getElementById('filter-benefit-emote')) document.getElementById('filter-benefit-emote').checked = settings.inventory_filters.show_benefit_emote !== false;
        if (document.getElementById('filter-benefit-other')) document.getElementById('filter-benefit-other').checked = settings.inventory_filters.show_benefit_other !== false;
    }

    // Restore inventory view mode (by game / by category) + per-section collapse state
    if (settings.inventory_ui) {
        setInventoryViewModeUI(settings.inventory_ui.categories_enabled ? 'category' : 'game');
        if (settings.inventory_ui.sections) {
            Object.assign(state.inventorySections, settings.inventory_ui.sections);
        }
    }

    // Restore mining benefit filters
    if (settings.mining_benefits) {
        if (document.getElementById('mining-benefit-item')) document.getElementById('mining-benefit-item').checked = settings.mining_benefits.DIRECT_ENTITLEMENT;
        if (document.getElementById('mining-benefit-badge')) document.getElementById('mining-benefit-badge').checked = settings.mining_benefits.BADGE;
        if (document.getElementById('mining-benefit-emote')) document.getElementById('mining-benefit-emote').checked = settings.mining_benefits.EMOTE;
        if (document.getElementById('mining-benefit-unknown')) document.getElementById('mining-benefit-unknown').checked = settings.mining_benefits.UNKNOWN;
    }


    // Restore idle behavior settings
    const idleMineAllCheckbox = document.getElementById('idle-mine-all-when-idle');
    if (idleMineAllCheckbox) {
        idleMineAllCheckbox.checked = settings.idle_behavior?.mine_all_when_idle !== false;
    }

    // Restore library sync settings
    updateLibrarySyncUI(settings.library_sync);

    // Update games to watch lists
    renderGamesToWatch();

    // Re-render channels list to apply filter based on updated games to watch
    renderChannels();

    // Re-render inventory to apply filters
    renderInventory();
}

function updateManualModeUI(manualModeInfo) {
    const manualBadge = document.getElementById('manual-mode-badge');
    const autoBadge = document.getElementById('auto-mode-badge');
    const manualGameName = document.getElementById('manual-game-name');
    const manualControls = document.getElementById('manual-mode-controls');
    const manualModeGame = document.getElementById('manual-mode-game');

    if (manualModeInfo.active) {
        // Show manual mode badge, hide auto badge
        manualBadge.classList.remove('hidden');
        autoBadge.classList.add('hidden');
        manualGameName.textContent = manualModeInfo.game_name || '';

        // Show manual mode controls in drop progress section
        if (manualControls) {
            manualControls.classList.remove('hidden');
            if (manualModeGame) {
                manualModeGame.textContent = manualModeInfo.game_name || '';
            }
        }
    } else {
        // Hide manual mode badge, show auto badge
        manualBadge.classList.add('hidden');
        autoBadge.classList.remove('hidden');

        // Hide manual mode controls
        if (manualControls) {
            manualControls.classList.add('hidden');
        }
    }
}

// ==================== Games to Watch Management ====================

let availableGames = new Set(); // All games from campaigns
let draggedElement = null;

socket.on('games_available', (data) => {
    availableGames = new Set(data.games || []);
    renderGamesToWatch();
});

function renderGamesToWatch() {
    const selectedGames = state.settings.games_to_watch || [];
    const filterText = document.getElementById('games-filter')?.value.toLowerCase() || '';

    // Render selected games (sortable)
    renderSelectedGames(selectedGames);

    // Render available games (checkboxes for unselected games)
    const unselectedGames = Array.from(availableGames)
        .filter(game => !selectedGames.includes(game))
        .filter(game => game.toLowerCase().includes(filterText))
        .sort();

    renderAvailableGames(unselectedGames, filterText);
}

function renderSelectedGames(games) {
    const container = document.getElementById('selected-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        const emptyMsg = t.gui?.settings?.no_games_selected || 'No games selected. Check games below to add them.';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    games.forEach((game, index) => {
        const div = document.createElement('div');
        div.className = 'sortable-item';
        div.draggable = true;
        div.dataset.game = game;
        div.replaceChildren(
            makeElement('span', { class: 'priority-number' }, String(index + 1)),
            makeElement('span', { class: 'game-name' }, game),
            makeElement('button', { class: 'remove-btn' }, '✕'),
        );

        // Event listener for the delete button
        const removeBtn = div.querySelector('.remove-btn');
        removeBtn.addEventListener('click', () => removeGameFromWatch(game));

        // Drag event handlers (reordering/cross-list drop handled at the container level)
        div.addEventListener('dragstart', handleDragStart);
        div.addEventListener('dragend', handleDragEnd);

        container.appendChild(div);
    });
}

function renderAvailableGames(games, filterText) {
    const container = document.getElementById('available-games-list');
    if (!container) return;

    const t = state.translations;
    container.innerHTML = '';

    if (games.length === 0) {
        if (filterText) {
            const emptyMsg = t.gui?.settings?.no_games_match || 'No games match your search.';
            const addHint = t.gui?.settings?.add_game_hint || ' Click "Add Game" to add it manually.';
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, `${emptyMsg}${addHint}`));
        } else {
            const emptyMsg = t.gui?.settings?.all_games_selected || 'All games are selected or no games available.';
            container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        }
        return;
    }

    games.forEach(game => {
        const div = document.createElement('div');
        div.className = 'sortable-item available-game-item';
        div.draggable = true;
        div.dataset.game = game;
        const addBtn = makeElement('button', { class: 'add-btn', title: 'Add to tracklist' }, null, (btn) => {
            btn.appendChild(makeElement('span', { class: 'add-btn-icon' }, '✕'));
        });
        div.replaceChildren(
            makeElement('span', { class: 'game-name' }, game),
            addBtn,
        );

        addBtn.addEventListener('click', () => toggleGameWatch(game, true));

        // Drag event handlers (reordering/cross-list drop handled at the container level)
        div.addEventListener('dragstart', handleDragStart);
        div.addEventListener('dragend', handleDragEnd);

        container.appendChild(div);
    });
}

// Drag and drop handlers - unified between the selected and available games
// lists so an item can be dragged either way: from "Selected" to "Available"
// to remove it from the tracklist, or from "Available" to "Selected" (at a
// specific position) to add it.
function handleDragStart(e) {
    draggedElement = e.target;
    e.target.classList.add('dragging');
    // Suppress hover highlight/transitions on all items while dragging - they
    // fight with the live reordering below and cause visible flicker
    document.body.classList.add('dnd-active');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', e.target.innerHTML);
}

// Repositioning is batched to at most once per animation frame (dragover
// fires far more often than the screen can repaint) and skipped entirely
// when it wouldn't actually change the DOM order - both are needed to stop
// the dragged item's preview from jittering/flickering while hovering.
let dragOverRaf = null;

// Determine which existing item the dragged element should be inserted before,
// based on the cursor's vertical position within the container. Returns null
// if it should be appended at the end.
function getDragAfterElement(container, y) {
    const items = [...container.querySelectorAll('.sortable-item:not(.dragging)')];

    return items.reduce((closest, item) => {
        const box = item.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
            return { offset, element: item };
        }
        return closest;
    }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
}

// Attached once to both list containers - handles hovering over items,
// empty space below the last item, and empty lists alike.
function handleContainerDragOver(e) {
    e.preventDefault();
    if (!draggedElement) return;
    e.dataTransfer.dropEffect = 'move';

    const container = e.currentTarget;
    const clientY = e.clientY;

    if (dragOverRaf !== null) return;
    dragOverRaf = requestAnimationFrame(() => {
        dragOverRaf = null;
        if (!draggedElement) return;

        // The "empty" placeholder message would otherwise block the drop target
        container.querySelector('.empty-message')?.remove();

        const afterElement = getDragAfterElement(container, clientY);
        if (afterElement == null) {
            // Only move if it isn't already the last item, otherwise this is a no-op
            // insert that still triggers a reflow (and the resulting flicker)
            if (container.lastElementChild !== draggedElement) {
                container.appendChild(draggedElement);
            }
        } else if (afterElement !== draggedElement && afterElement.previousElementSibling !== draggedElement) {
            container.insertBefore(draggedElement, afterElement);
        }
    });
}

function handleContainerDrop(e) {
    e.preventDefault();
    if (e.stopPropagation) {
        e.stopPropagation();
    }
    return false;
}

function handleDragEnd(e) {
    e.target.classList.remove('dragging');
    document.body.classList.remove('dnd-active');
    if (dragOverRaf !== null) {
        cancelAnimationFrame(dragOverRaf);
        dragOverRaf = null;
    }
    draggedElement = null;

    const finalContainer = e.target.parentNode;
    const gameName = e.target.dataset.game;

    if (finalContainer && finalContainer.id === 'selected-games-list') {
        // Dropped into (or reordered within) the tracklist: rebuild the order
        // from the current DOM order, which also covers games newly dragged
        // in from the available games list.
        const items = finalContainer.querySelectorAll('.sortable-item');
        state.settings.games_to_watch = Array.from(items).map(item => item.dataset.game);
    } else {
        // Dropped onto the available games list (or anywhere else): remove it
        // from the watch list, if it was on it.
        const games = state.settings.games_to_watch || [];
        const index = games.indexOf(gameName);
        if (index === -1) {
            // Nothing changed (e.g. just reordered within the available games list)
            renderGamesToWatch();
            return;
        }
        games.splice(index, 1);
        state.settings.games_to_watch = games;
    }

    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

// Wires up the container-level drag-over/drop handlers; called once on init
// since the containers themselves persist across re-renders.
function setupGamesDragAndDrop() {
    for (const id of ['selected-games-list', 'available-games-list']) {
        const container = document.getElementById(id);
        if (!container) continue;
        container.addEventListener('dragover', handleContainerDragOver);
        container.addEventListener('drop', handleContainerDrop);
    }
}


function toggleGameWatch(gameName, checked) {
    const games = state.settings.games_to_watch || [];

    if (checked && !games.includes(gameName)) {
        games.push(gameName);
    } else if (!checked) {
        const index = games.indexOf(gameName);
        if (index > -1) {
            games.splice(index, 1);
        }
    }

    state.settings.games_to_watch = games;
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function removeGameFromWatch(gameName) {
    const games = state.settings.games_to_watch || [];
    const index = games.indexOf(gameName);
    if (index > -1) {
        games.splice(index, 1);
        state.settings.games_to_watch = games;
        renderGamesToWatch();
        renderChannels();
        saveSettings();
    }
}

function selectAllGames() {
    state.settings.games_to_watch = Array.from(availableGames).sort();
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function deselectAllGames() {
    state.settings.games_to_watch = [];
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

function addGameFromSearch() {
    const searchInput = document.getElementById('games-filter');
    const gameName = searchInput.value.trim();

    if (!gameName) {
        return;
    }

    const games = state.settings.games_to_watch || [];
    
    // Check if already selected
    if (games.includes(gameName)) {
        searchInput.value = ''; // Clear input if already added
        renderGamesToWatch(); // Just re-render to clear any filtering state if needed
        return;
    }

    // Add to selected games
    games.push(gameName);
    state.settings.games_to_watch = games;

    // Add to available games set so it shows up in lists
    availableGames.add(gameName);

    // Clear search and update UI
    searchInput.value = '';
    renderGamesToWatch();
    renderChannels();
    saveSettings();
}

// ==================== Game Library Sync ====================

const LIBRARY_PICKER_MAX_ROWS = 200;

function getLibraryModeFromUI() {
    return document.getElementById('library-mode-whitelist')?.checked ? 'whitelist' : 'blacklist';
}

function getActiveLibraryList() {
    const ls = state.settings.library_sync || (state.settings.library_sync = {});
    const mode = getLibraryModeFromUI();
    if (!Array.isArray(ls[mode])) ls[mode] = [];
    return ls[mode];
}

function updateLibraryModeDesc() {
    const desc = document.getElementById('library-mode-desc');
    if (!desc) return;
    const library = state.translations.gui?.settings?.library || {};
    if (getLibraryModeFromUI() === 'whitelist') {
        desc.textContent = library.mode_whitelist_desc
            || 'Only the owned games selected below are watched automatically.';
    } else {
        desc.textContent = library.mode_blacklist_desc
            || 'All owned games with an active campaign are watched automatically - except the games selected below.';
    }
}

function updateLibrarySyncUI(librarySync) {
    if (!librarySync) return;

    const enabledCheckbox = document.getElementById('library-sync-enabled');
    if (enabledCheckbox) enabledCheckbox.checked = librarySync.enabled || false;
    updateLibraryOptionsVisibility();

    const steam = librarySync.steam || {};
    const steamEnabled = document.getElementById('steam-sync-enabled');
    if (steamEnabled) steamEnabled.checked = steam.enabled || false;
    // don't clobber fields the user is currently typing in with save echoes
    const steamApiKey = document.getElementById('steam-api-key');
    if (steamApiKey && document.activeElement !== steamApiKey) steamApiKey.value = steam.api_key || '';
    const steamId = document.getElementById('steam-id');
    if (steamId && document.activeElement !== steamId) steamId.value = steam.steam_id || '';

    const ubisoft = librarySync.ubisoft || {};
    const ubisoftEnabled = document.getElementById('ubisoft-sync-enabled');
    if (ubisoftEnabled) ubisoftEnabled.checked = ubisoft.enabled || false;
    const ubisoftTicket = document.getElementById('ubisoft-ticket');
    if (ubisoftTicket && document.activeElement !== ubisoftTicket) ubisoftTicket.value = ubisoft.remember_me_ticket || '';

    // keep the provider status lines in sync with the new configuration
    fetchLibraryStatus();

    const isWhitelist = librarySync.list_mode === 'whitelist';
    const blacklistRadio = document.getElementById('library-mode-blacklist');
    const whitelistRadio = document.getElementById('library-mode-whitelist');
    if (blacklistRadio) blacklistRadio.checked = !isWhitelist;
    if (whitelistRadio) whitelistRadio.checked = isWhitelist;

    updateLibraryModeDesc();
    renderLibraryPicker();
}

function updateLibraryOptionsVisibility() {
    const options = document.getElementById('library-sync-options');
    const enabled = document.getElementById('library-sync-enabled')?.checked;
    if (options) options.classList.toggle('library-disabled', !enabled);
}

function getLibrarySyncFromUI() {
    const ls = state.settings.library_sync || {};
    return {
        enabled: document.getElementById('library-sync-enabled')?.checked || false,
        list_mode: getLibraryModeFromUI(),
        blacklist: ls.blacklist || [],
        whitelist: ls.whitelist || [],
        steam: {
            enabled: document.getElementById('steam-sync-enabled')?.checked || false,
            api_key: document.getElementById('steam-api-key')?.value.trim() || '',
            steam_id: document.getElementById('steam-id')?.value.trim() || '',
        },
        ubisoft: {
            enabled: document.getElementById('ubisoft-sync-enabled')?.checked || false,
            remember_me_ticket: document.getElementById('ubisoft-ticket')?.value.trim() || '',
        },
    };
}

function onLibraryModeChange() {
    const ls = state.settings.library_sync || (state.settings.library_sync = {});
    ls.list_mode = getLibraryModeFromUI();
    updateLibraryModeDesc();
    renderLibraryPicker();
    saveSettings();
}

function toggleLibraryListGame(gameName, listed) {
    const list = getActiveLibraryList();
    const index = list.findIndex(name => name.toLowerCase() === gameName.toLowerCase());
    if (listed && index === -1) {
        list.push(gameName);
    } else if (!listed && index > -1) {
        list.splice(index, 1);
    }
    renderLibraryPicker();
    saveSettings();
}

function formatLastPlayed(timestamp) {
    if (!timestamp) return '';
    try {
        return new Date(timestamp * 1000).toLocaleDateString();
    } catch (e) {
        return '';
    }
}

function renderLibraryPicker() {
    renderLibraryChips();
    renderLibraryOwnedList();
}

function renderLibraryChips() {
    const container = document.getElementById('library-list-chips');
    if (!container) return;
    container.innerHTML = '';

    const list = getActiveLibraryList();
    if (list.length === 0) {
        container.style.display = 'none';
        return;
    }
    container.style.display = 'flex';

    list.forEach(game => {
        const chip = makeElement('span', { class: 'chip' }, null, el => {
            el.appendChild(makeElement('span', { class: 'chip-name' }, game));
            const removeBtn = makeElement('button', { class: 'chip-remove', title: 'Remove' }, '✕');
            removeBtn.addEventListener('click', () => toggleLibraryListGame(game, false));
            el.appendChild(removeBtn);
        });
        container.appendChild(chip);
    });
}

function renderLibraryOwnedList() {
    const container = document.getElementById('library-owned-list');
    if (!container) return;
    const library = state.translations.gui?.settings?.library || {};
    container.innerHTML = '';

    const owned = state.ownedGames || [];
    if (owned.length === 0) {
        const emptyMsg = library.no_owned_games
            || 'No games synced yet. Configure a platform above and click Sync Now.';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    const filterText = (document.getElementById('library-game-search')?.value || '').toLowerCase();
    const listedNames = new Set(getActiveLibraryList().map(name => name.toLowerCase()));
    const filtered = owned.filter(game => game.name.toLowerCase().includes(filterText));

    if (filtered.length === 0) {
        const emptyMsg = library.no_library_match || 'No games match your search.';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    const visible = filtered.slice(0, LIBRARY_PICKER_MAX_ROWS);
    visible.forEach(game => {
        const listed = listedNames.has(game.name.toLowerCase());
        const row = makeElement('label', { class: 'library-game-row' }, null, el => {
            const checkbox = makeElement('input', { type: 'checkbox' });
            checkbox.checked = listed;
            checkbox.addEventListener('change', (e) => toggleLibraryListGame(game.name, e.target.checked));
            el.appendChild(checkbox);
            el.appendChild(makeElement('span', { class: 'library-game-name' }, game.name));
            const lastPlayed = formatLastPlayed(game.last_played);
            if (lastPlayed) {
                el.appendChild(makeElement('span', { class: 'library-game-played' }, lastPlayed));
            }
        });
        container.appendChild(row);
    });

    if (filtered.length > visible.length) {
        const moreTemplate = library.more_games || '…and {count} more - refine your search';
        container.appendChild(makeElement(
            'p', { class: 'empty-message' },
            moreTemplate.replace('{count}', String(filtered.length - visible.length))
        ));
    }
}

async function fetchOwnedGames() {
    try {
        const response = await fetch('/api/library/games');
        const data = await response.json();
        state.ownedGames = data.games || [];
        renderLibraryPicker();
    } catch (error) {
        console.error('Failed to fetch owned games:', error);
    }
}

async function fetchLibraryStatus() {
    try {
        const response = await fetch('/api/library/status');
        updateProviderStatusLines(await response.json());
    } catch (error) {
        console.error('Failed to fetch library status:', error);
    }
}

const LIBRARY_PROVIDERS = ['steam', 'ubisoft'];

function updateProviderStatusLines(status) {
    const library = state.translations.gui?.settings?.library || {};
    LIBRARY_PROVIDERS.forEach(providerName => {
        const line = document.getElementById(`${providerName}-status-line`);
        if (!line) return;
        const provider = status?.providers?.[providerName];

        line.classList.remove('status-ok', 'status-error');
        if (!provider || !provider.configured) {
            line.textContent = library.not_configured || 'Not configured';
            return;
        }
        if (provider.last_error) {
            line.classList.add('status-error');
            line.textContent = provider.last_error;
            return;
        }
        const parts = [`${provider.game_count} ${library.owned_games || 'owned games'}`];
        if (provider.last_sync) {
            const lastSyncLabel = library.last_sync || 'Last sync:';
            parts.push(`${lastSyncLabel} ${new Date(provider.last_sync).toLocaleString()}`);
        } else {
            parts.push(library.never_synced || 'Never synced');
        }
        line.classList.add('status-ok');
        line.textContent = parts.join(' • ');
    });
}

function renderAutoWatchList() {
    const container = document.getElementById('auto-watch-list');
    if (!container) return;
    const t = state.translations;
    const library = t.gui?.settings?.library || {};

    container.innerHTML = '';
    const games = state.autoWatchGames || [];
    if (games.length === 0) {
        const emptyMsg = library.auto_list_empty || 'No games auto-added yet.';
        container.replaceChildren(makeElement('p', { class: 'empty-message' }, emptyMsg));
        return;
    }

    games.forEach((game, index) => {
        container.appendChild(makeElement('div', { class: 'auto-watch-item' }, null, el => {
            el.appendChild(makeElement('span', { class: 'priority-number' }, String(index + 1)));
            el.appendChild(makeElement('span', { class: 'game-name' }, game));
        }));
    });
}

function renderLibrarySyncStatus(data) {
    const statusDiv = document.getElementById('library-sync-status');
    if (!statusDiv) return;
    const t = state.translations;
    const library = t.gui?.settings?.library || {};

    statusDiv.style.display = 'block';
    if (!data.success) {
        statusDiv.className = 'verify-result error';
        statusDiv.textContent = `✗ ${data.message || library.sync_disabled || 'Library sync is disabled.'}`;
        return;
    }

    const parts = [];
    let hasError = false;
    const providers = data.status?.providers || {};
    Object.entries(providers).forEach(([name, provider]) => {
        if (!provider.enabled) return;
        const providerName = name.charAt(0).toUpperCase() + name.slice(1);
        if (provider.last_error) {
            hasError = true;
            parts.push(`✗ ${provider.last_error}`);
        } else {
            const ownedGames = library.owned_games || 'owned games';
            parts.push(`✓ ${providerName}: ${provider.game_count} ${ownedGames}`);
        }
    });

    const added = data.added_games || [];
    if (added.length > 0) {
        const addedTemplate = library.added_games || 'Added: {games}';
        parts.push(addedTemplate.replace('{games}', added.join(', ')));
    } else if (!hasError) {
        parts.push(library.no_new_games || 'No new games to add.');
    }

    statusDiv.className = hasError ? 'verify-result error' : 'verify-result success';
    statusDiv.textContent = parts.join(' • ');
}

async function syncLibraryNow() {
    const statusDiv = document.getElementById('library-sync-status');
    const t = state.translations;
    if (statusDiv) {
        statusDiv.style.display = 'block';
        statusDiv.className = 'verify-result loading';
        statusDiv.textContent = t.gui?.settings?.library?.syncing || 'Syncing...';
    }

    try {
        // make sure the backend syncs with the freshest configuration
        await saveSettings();
        const response = await fetch('/api/library/sync', { method: 'POST' });
        const data = await response.json();
        if (data.auto_watch_games) {
            state.autoWatchGames = data.auto_watch_games;
            renderAutoWatchList();
            renderChannels();
        }
        renderLibrarySyncStatus(data);
        if (data.status) updateProviderStatusLines(data.status);
        // refresh the owned-games picker with the newly synced library
        fetchOwnedGames();
    } catch (error) {
        if (statusDiv) {
            statusDiv.className = 'verify-result error';
            statusDiv.textContent = `Error: ${error.message}`;
        }
    }
}

function flashTitle() {
    const originalTitle = document.title;
    let count = 0;
    const interval = setInterval(() => {
        document.title = count % 2 === 0 ? '🔔 Attention!' : originalTitle;
        count++;
        if (count >= 10) {
            document.title = originalTitle;
            clearInterval(interval);
        }
    }, 1000);
}

// ==================== API Functions ====================

async function selectChannel(channelId) {
    try {
        const response = await fetch('/api/channels/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId })
        });

        if (!response.ok) {
            const errorData = await response.json();
            console.error('Failed to select channel:', errorData.detail || 'Unknown error');
            addConsoleLine(`Error selecting channel: ${errorData.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Failed to select channel:', error);
        addConsoleLine(`Error selecting channel: ${error.message}`);
    }
}

async function exitManualMode() {
    try {
        const response = await fetch('/api/mode/exit-manual', {
            method: 'POST'
        });

        const result = await response.json();
        if (!result.success) {
            console.log('Exit manual mode:', result.message || 'Already in automatic mode');
        }
    } catch (error) {
        console.error('Failed to exit manual mode:', error);
        addConsoleLine(`Error exiting manual mode: ${error.message}`);
    }
}

async function submitLogin() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const token = document.getElementById('2fa-token').value;

    try {
        await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, token })
        });
    } catch (error) {
        console.error('Failed to submit login:', error);
    }
}

async function confirmOAuth() {
    // Signal that OAuth code has been entered
    try {
        await fetch('/api/oauth/confirm', {
            method: 'POST'
        });
        // Hide the OAuth form and show waiting message
        document.getElementById('oauth-code-display').style.display = 'none';
        const t = state.translations;
        const waitingAuth = t.gui?.login?.waiting_auth || 'Waiting for authentication...';
        const loginStatus = document.getElementById('login-status');
        loginStatus.textContent = waitingAuth;
        loginStatus.setAttribute('translation-key', 'waiting_auth');
    } catch (error) {
        console.error('Failed to confirm OAuth:', error);
    }
}

async function verifyProxy() {
    const proxyInput = document.getElementById('proxy-url');
    const proxyUrl = proxyInput ? proxyInput.value.trim() : '';
    const resultDiv = document.getElementById('proxy-verify-result');

    if (!resultDiv) return;

    // Reset display
    resultDiv.style.display = 'block';
    resultDiv.className = 'verify-result loading';
    resultDiv.textContent = 'Verifying connection...';

    if (!proxyUrl) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = 'Please enter a proxy URL first.';
        return;
    }

    try {
        const response = await fetch('/api/settings/verify-proxy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy: proxyUrl })
        });

        const data = await response.json();

        if (data.success) {
            resultDiv.className = 'verify-result success';
            resultDiv.textContent = `✓ ${data.message}`;
        } else {
            resultDiv.className = 'verify-result error';
            resultDiv.textContent = `✗ ${data.message}`;
        }
    } catch (error) {
        resultDiv.className = 'verify-result error';
        resultDiv.textContent = `Error: ${error.message}`;
    }
}

async function saveSettings() {
    const settings = {
        dark_mode: getDarkModeFromUI(),
        animations: getAnimationsModeFromUI(),
        // the dropdown is empty until languages are fetched - never send ""
        language: document.getElementById('language')?.value || undefined,
        connection_quality: parseInt(document.getElementById('connection-quality').value),
        minimum_refresh_interval_minutes: parseInt(document.getElementById('minimum-refresh-interval').value),
        proxy: state.settings.proxy || '',
        games_to_watch: state.settings.games_to_watch || [],
        idle_behavior: {
            mine_all_when_idle: document.getElementById('idle-mine-all-when-idle')?.checked !== false
        },
        inventory_filters: getInventoryFilters(),
        inventory_ui: {
            categories_enabled: getInventoryViewModeFromUI() === 'category',
            sections: { ...state.inventorySections }
        },
        mining_benefits: {
            "DIRECT_ENTITLEMENT": document.getElementById('mining-benefit-item')?.checked,
            "BADGE": document.getElementById('mining-benefit-badge')?.checked,
            "EMOTE": document.getElementById('mining-benefit-emote')?.checked,
            "UNKNOWN": document.getElementById('mining-benefit-unknown')?.checked
        },
        library_sync: getLibrarySyncFromUI()
    };
    state.settings.library_sync = settings.library_sync;

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        console.log('Settings saved automatically');
    } catch (error) {
        console.error('Failed to save settings:', error);
    }
}

async function fetchAndPopulateLanguages() {
    try {
        const response = await fetch('/api/languages');
        const data = await response.json();

        const languageSelect = document.getElementById('language');
        if (!languageSelect) {
            console.warn('Language select element not found');
            return;
        }

        // Clear existing options
        languageSelect.innerHTML = '';

        // Populate with available languages
        data.available.forEach(lang => {
            const option = document.createElement('option');
            option.value = lang;
            option.textContent = lang;
            languageSelect.appendChild(option);
        });

        // Set current language
        if (data.current) {
            languageSelect.value = data.current;
        }
    } catch (error) {
        console.error('Failed to fetch languages:', error);
        const languageSelect = document.getElementById('language');
        if (languageSelect) {
            languageSelect.replaceChildren(makeElement('option', { value: '' }, 'Failed to load languages'));
        }
        addConsoleLine('Error: Unable to fetch available languages. Please check your connection or try again later.');
    }
}

async function fetchAndApplyTranslations() {
    try {
        const response = await fetch('/api/translations');
        const data = await response.json();

        state.translations = data;
        applyTranslations(data);
        console.log('Translations applied for language:', data.language_name);
    } catch (error) {
        console.error('Failed to fetch translations:', error);
    }
}

function applyTranslations(t) {
    // Update tab buttons
    const tabButtons = {
        'main': document.querySelector('[data-tab="main"]'),
        'inventory': document.querySelector('[data-tab="inventory"]'),
        'settings': document.querySelector('[data-tab="settings"]')
    };

    if (tabButtons.main && t.gui?.tabs) tabButtons.main.textContent = t.gui.tabs.main;
    if (tabButtons.inventory && t.gui?.tabs) tabButtons.inventory.textContent = t.gui.tabs.inventory;
    if (tabButtons.settings && t.gui?.tabs) tabButtons.settings.textContent = t.gui.tabs.settings;

    // Update Main tab - Login section
    const mainTab = document.getElementById('main-tab');
    if (mainTab && t.gui?.login) {
        const loginHeader = mainTab.querySelector('.login-panel h2');
        if (loginHeader) loginHeader.textContent = t.gui.login.name;

        const loginStatus = document.getElementById('login-status');
        if (loginStatus?.hasAttribute('translation-key')) loginStatus.textContent = t.login?.status?.[loginStatus.getAttribute('translation-key')];

        // Update login form placeholders
        const usernameInput = document.getElementById('username');
        if (usernameInput) usernameInput.placeholder = t.gui.login.username;

        const passwordInput = document.getElementById('password');
        if (passwordInput) passwordInput.placeholder = t.gui.login.password;

        const twofaInput = document.getElementById('2fa-token');
        if (twofaInput) twofaInput.placeholder = t.gui.login.twofa_code;

        const loginButton = document.getElementById('login-button');
        if (loginButton) loginButton.textContent = t.gui.login.button;

        // Update OAuth display text
        const oauthDisplay = document.getElementById('oauth-code-display');
        if (oauthDisplay) {
            const oauthP = oauthDisplay.querySelector('p');
            if (oauthP) {
                const link = oauthP.querySelector('a');
                if (link) {
                    oauthP.textContent = t.gui.login.oauth_prompt + ' ';
                    link.textContent = t.gui.login.oauth_activate;
                    oauthP.appendChild(link);
                }
            }

            const oauthConfirmBtn = document.getElementById('oauth-confirm');
            if (oauthConfirmBtn) oauthConfirmBtn.textContent = t.gui.login.oauth_confirm;
        }
    }

    // Update Progress section
    if (mainTab && t.gui?.progress) {
        // ID: progress-header
        const progressHeader = document.getElementById('progress-header');
        if (progressHeader) progressHeader.textContent = t.gui.progress.name;

        const noDropMsg = document.getElementById('no-drop-message');
        if (noDropMsg) noDropMsg.textContent = t.gui.progress.no_drop;

        const exitManualBtn = document.getElementById('exit-manual-btn');
        if (exitManualBtn) exitManualBtn.textContent = t.gui.progress.return_to_auto;
    }

    // Update Console section
    if (mainTab && t.gui) {
        // ID: console-header
        const consoleHeader = document.getElementById('console-header');
        if (consoleHeader) consoleHeader.textContent = t.gui.output;
    }

    // Update Channels section
    if (mainTab && t.gui?.channels) {
        // ID: channels-header
        const channelsHeader = document.getElementById('channels-header');
        if (channelsHeader) channelsHeader.textContent = t.gui.channels.name;
        // Channel list will re-render with translated empty messages
        renderChannels();
    }

    // Update Inventory tab
    const inventoryTab = document.getElementById('inventory-tab');
    if (inventoryTab && t.gui?.inventory) {
        // Inventory will re-render with translated status and empty messages
        renderInventory();
    }

    // Update Settings tab
    const settingsTab = document.getElementById('settings-tab');
    if (settingsTab && t.gui?.settings) {
        // Use IDs for robust selection
        const generalHeader = document.getElementById('settings-general-header');
        if (generalHeader) generalHeader.textContent = t.gui.settings.general.name;

        const benefitsHeader = document.getElementById('settings-benefits-header');
        if (benefitsHeader && t.gui.settings.mining_benefits) benefitsHeader.textContent = t.gui.settings.mining_benefits;

        const gamesHeader = document.getElementById('settings-games-header');
        if (gamesHeader) gamesHeader.textContent = t.gui.settings.games_to_watch;

        const actionsHeader = document.getElementById('settings-actions-header');
        if (actionsHeader) actionsHeader.textContent = t.gui.settings.actions;

        const appearanceHeader = document.getElementById('settings-appearance-header');
        if (appearanceHeader && t.gui.settings.appearance) appearanceHeader.textContent = t.gui.settings.appearance.name;

        const darkMode = t.gui.settings.appearance?.dark_mode;
        if (darkMode) {
            const darkModeHeader = document.getElementById('dark-mode-header');
            if (darkModeHeader) darkModeHeader.textContent = darkMode.name;
            const darkModeAutoLabel = document.getElementById('dark-mode-auto-label');
            if (darkModeAutoLabel) darkModeAutoLabel.textContent = darkMode.auto;
            const darkModeOnLabel = document.getElementById('dark-mode-on-label');
            if (darkModeOnLabel) darkModeOnLabel.textContent = darkMode.on;
            const darkModeOffLabel = document.getElementById('dark-mode-off-label');
            if (darkModeOffLabel) darkModeOffLabel.textContent = darkMode.off;
        }

        const animations = t.gui.settings.appearance?.animations;
        if (animations) {
            const animationsHeader = document.getElementById('animations-header');
            if (animationsHeader) animationsHeader.textContent = animations.name;
            const autoLabel = document.getElementById('animations-auto-label');
            if (autoLabel) autoLabel.textContent = animations.auto;
            const onLabel = document.getElementById('animations-on-label');
            if (onLabel) onLabel.textContent = animations.on;
            const offLabel = document.getElementById('animations-off-label');
            if (offLabel) offLabel.textContent = animations.off;
        }

        const connQualityLabelText = document.getElementById('connection-quality-label-text');
        if (connQualityLabelText) connQualityLabelText.textContent = t.gui.settings.connection_quality;

        const refreshLabelText = document.getElementById('minimum-refresh-label-text');
        if (refreshLabelText) refreshLabelText.textContent = t.gui.settings.minimum_refresh;

        const proxy = t.gui.settings.proxy;
        if (proxy) {
            const proxyLabelText = document.getElementById('proxy-url-label-text');
            if (proxyLabelText && proxy.name) proxyLabelText.textContent = proxy.name;
            const proxyHelpText = document.getElementById('proxy-help-text');
            if (proxyHelpText && proxy.help) proxyHelpText.textContent = proxy.help;
            const setProxyBtn = document.getElementById('set-proxy-btn');
            if (setProxyBtn && proxy.set) setProxyBtn.textContent = proxy.set;
            const verifyProxyBtn = document.getElementById('verify-proxy-btn');
            if (verifyProxyBtn && proxy.verify) verifyProxyBtn.textContent = proxy.verify;
        }

        const benefitsHelp = document.getElementById('settings-benefits-help');
        if (benefitsHelp && t.gui.settings.mining_benefits_help) benefitsHelp.textContent = t.gui.settings.mining_benefits_help;

        const gamesHelp = document.getElementById('settings-games-help');
        if (gamesHelp) gamesHelp.textContent = t.gui.settings.games_help;

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = t.gui.settings.search_games;

        const selectAllBtn = document.getElementById('select-all-btn');
        if (selectAllBtn) selectAllBtn.textContent = t.gui.settings.select_all;

        const deselectAllBtn = document.getElementById('deselect-all-btn');
        if (deselectAllBtn) deselectAllBtn.textContent = t.gui.settings.deselect_all;

        const addGameBtn = document.getElementById('add-game-btn');
        if (addGameBtn && t.gui.settings.add_game) addGameBtn.textContent = t.gui.settings.add_game;

        const selectedGamesHeader = settingsTab.querySelector('.selected-games h3');
        if (selectedGamesHeader) selectedGamesHeader.textContent = t.gui.settings.selected_games;

        const availableGamesHeader = settingsTab.querySelector('.available-games h3');
        if (availableGamesHeader) availableGamesHeader.textContent = t.gui.settings.available_games;

        const reloadBtn = document.getElementById('reload-btn');
        if (reloadBtn) reloadBtn.textContent = t.gui.settings.reload_campaigns;

        // Idle behavior section
        const idleBehavior = t.gui.settings.idle_behavior;
        if (idleBehavior) {
            const idleHeader = document.getElementById('settings-idle-header');
            if (idleHeader && idleBehavior.name) idleHeader.textContent = idleBehavior.name;
            const idleHelp = document.getElementById('settings-idle-help');
            if (idleHelp && idleBehavior.help) idleHelp.textContent = idleBehavior.help;
        }

        // Library sync section
        const library = t.gui.settings.library;
        if (library) {
            const setLibraryText = (id, value) => {
                const el = document.getElementById(id);
                if (el && value) el.textContent = value;
            };
            setLibraryText('settings-library-header', library.name);
            setLibraryText('settings-library-help', library.help);
            setLibraryText('library-steam-header', library.steam);
            setLibraryText('steam-api-key-label', library.steam_api_key);
            setLibraryText('steam-id-label', library.steam_id);
            setLibraryText('library-ubisoft-header', library.ubisoft);
            setLibraryText('ubisoft-ticket-label', library.ubisoft_ticket);
            // ubisoft-hint stays untranslated HTML - it contains the login link
            setLibraryText('library-mode-header', library.mode);
            setLibraryText('library-mode-blacklist-label', library.mode_blacklist_name);
            setLibraryText('library-mode-whitelist-label', library.mode_whitelist_name);
            setLibraryText('library-sync-now-btn', library.sync_now);
            setLibraryText('auto-watch-header', library.auto_list_label);
            const librarySearch = document.getElementById('library-game-search');
            if (librarySearch && library.search_library) librarySearch.placeholder = library.search_library;
            updateLibraryModeDesc();
            // re-render lists with translated empty messages
            renderLibraryPicker();
            renderAutoWatchList();
        }

        // Re-render games to watch with translated empty messages
        renderGamesToWatch();
    }

    // Update Footer
    if (t.gui?.footer) {
        const loadingText = t.gui.footer.loading || 'Loading...';
        const currentVersionEl = document.getElementById('current-version');
        // Only update if it's the specific "Loading..." text to avoid overwriting the fetched version
        if (currentVersionEl && currentVersionEl.textContent === 'Loading...') {
            currentVersionEl.textContent = loadingText;
        }

        const footerVersionText = document.getElementById('footer-version-text');
        if (footerVersionText) {
            const versionLabel = t.gui.footer.version || 'Version:';
            const span = document.getElementById('current-version'); // Need to re-fetch or preserve
            footerVersionText.textContent = versionLabel + ' ';
            // Re-finding the span because textContent wiped it from parent
            if (span) footerVersionText.appendChild(span);
        }
    }

    // Update Badges tooltips
    if (t.gui?.badges) {
        const manualBadge = document.getElementById('manual-mode-badge');
        if (manualBadge && t.gui.badges.manual) manualBadge.title = t.gui.badges.manual.title;

        const autoBadge = document.getElementById('auto-mode-badge');
        if (autoBadge && t.gui.badges.auto) autoBadge.title = t.gui.badges.auto.title;

        const proxyBadge = document.getElementById('proxy-indicator');
        if (proxyBadge && t.gui.badges.proxy) proxyBadge.title = t.gui.badges.proxy.title; // Note: append logic in updateSettingsUI overrides this
    }

    // Update Wanted Drops Panel
    if (mainTab && t.gui?.wanted) {
        // ID: wanted-header
        const wantedHeader = document.getElementById('wanted-header');
        if (wantedHeader) wantedHeader.textContent = t.gui.wanted.name;
        // Re-render wanted items to update empty message
        // Since we don't store wanted items in state globally (only receives them), we rely on updateWantedItems triggering render

        const unlinkedAutoHeader = document.getElementById('unlinked-auto-header');
        if (unlinkedAutoHeader) unlinkedAutoHeader.textContent = t.gui.wanted.unlinked_auto?.name || unlinkedAutoHeader.textContent;
    }

    // Update Inventory Filters (re-using existing inventoryTab variable if available, or just querying)
    // Note: inventoryTab was declared above in "Update Inventory Status" section
    // But since that might be in a different block or not, let's be safe and just query element directly without const redeclaration if it conflicts.
    // However, looking at the code, the previous declaration was likely in the same function scope.
    // Simplest fix: use the existing element or re-query without 'const' if needed, but best to just use the one we have.
    // Actually, looking at the view_file, there was 'const inventoryTab' around line 1639.
    // So I should just reuse that variable or use a different name.

    if (inventoryTab && t.gui?.inventory?.filters) {
        const f = t.gui.inventory.filters;
        const updateLabel = (id, text) => {
            const el = document.getElementById(id)?.parentElement.querySelector('span');
            if (el) el.textContent = text;
        };
        updateLabel('filter-active', f.active);
        updateLabel('filter-not-linked', f.not_linked);
        updateLabel('filter-upcoming', f.upcoming);
        updateLabel('filter-expired', f.expired);
        updateLabel('filter-finished', f.finished);
        updateLabel('filter-benefit-item', f.item);
        updateLabel('filter-benefit-badge', f.badge);
        updateLabel('filter-benefit-emote', f.emote);
        updateLabel('filter-benefit-other', f.other);

        const clearBtn = document.getElementById('clear-filters-btn');
        if (clearBtn) clearBtn.textContent = f.clear;

        const viewMode = t.gui.inventory.view_mode;
        if (viewMode) {
            const gameLabel = document.getElementById('inventory-view-mode-game-label');
            if (gameLabel && viewMode.game) gameLabel.textContent = viewMode.game;
            const categoryLabel = document.getElementById('inventory-view-mode-category-label');
            if (categoryLabel && viewMode.category) categoryLabel.textContent = viewMode.category;
        }

        const searchInput = document.getElementById('games-filter');
        if (searchInput) searchInput.placeholder = f.search_placeholder;

        // Update Mining Benefit Labels in Settings (re-using inventory filter keys)
        // IDs: mining-benefit-item, mining-benefit-badge, mining-benefit-emote, mining-benefit-unknown
        updateLabel('mining-benefit-item', f.item);
        updateLabel('mining-benefit-badge', f.badge);
        updateLabel('mining-benefit-emote', f.emote);
        updateLabel('mining-benefit-unknown', f.other);
    }

    // Update header elements
    if (t.gui?.header) {
        const languageLabel = document.querySelector('.language-selector span');
        if (languageLabel) languageLabel.textContent = t.gui.header.language;

        const statusText = document.getElementById('status-text');
        if (statusText && statusText.textContent === 'Initializing...') {
            statusText.textContent = t.gui.header.initializing;
        }

        // Update connection indicator
        const connIndicator = document.getElementById('connection-indicator');
        if (connIndicator) {
            if (state.connected) {
                connIndicator.textContent = '● ' + (t.gui.websocket.connected || 'Connected');
            } else {
                connIndicator.textContent = '● ' + (t.gui.websocket.disconnected || 'Disconnected');
            }
        }
    }
}

// ==================== Toast Notifications ====================

// info/success/warning auto-dismiss after their duration; error toasts have
// no entry here and stay until manually closed.
const TOAST_DURATIONS_MS = {
    info: 5000,
    success: 5000,
    warning: 8000
};

const TOAST_ICONS = {
    info: 'ℹ',
    success: '✓',
    warning: '⚠',
    error: '⛔'
};

/**
 * Show a toast notification.
 * @param {'info'|'success'|'warning'|'error'} type
 * @param {string} headline - Bold title line.
 * @param {string} [message] - Optional secondary body text.
 * @param {{duration?: number}} [opts] - Override the default auto-dismiss duration (ms).
 * @returns {HTMLElement} the toast element, in case the caller wants to dismiss it early.
 */
function showToast(type, headline, message, opts = {}) {
    const container = document.getElementById('toast-container');
    if (!container) return null;

    const kind = TOAST_ICONS[type] ? type : 'info';
    const persistent = kind === 'error';
    const closeLabel = state.translations.gui?.toasts?.close || 'Close';

    const progressBar = makeElement('div', { class: 'toast-progress-bar' });
    const toastEl = makeElement('div', { class: `toast ${kind}` }, '', el => {
        el.appendChild(makeElement('div', { class: 'toast-header' }, '', header => {
            header.appendChild(makeElement('span', { class: 'toast-icon' }, TOAST_ICONS[kind]));
            header.appendChild(makeElement('span', { class: 'toast-headline' }, headline || ''));
            header.appendChild(makeElement('button', { class: 'toast-close', type: 'button', title: closeLabel, 'aria-label': closeLabel }, '✕', btn => {
                btn.addEventListener('click', () => dismissToast(toastEl));
            }));
        }));
        if (message) {
            el.appendChild(makeElement('div', { class: 'toast-body' }, message));
        }
        if (!persistent) {
            el.appendChild(makeElement('div', { class: 'toast-progress-track' }, '', track => track.appendChild(progressBar)));
        }
    });

    container.appendChild(toastEl);

    if (!persistent) {
        const duration = opts.duration || TOAST_DURATIONS_MS[kind] || 5000;
        startToastTimer(toastEl, progressBar, duration);
    }

    return toastEl;
}

/**
 * Drive a toast's auto-dismiss countdown and its visible progress bar,
 * pausing both while the mouse hovers over the toast so users get a chance
 * to read it before it disappears.
 */
function startToastTimer(toastEl, progressBar, initialDuration) {
    let remaining = initialDuration;
    let startedAt = 0;
    let timerId = null;

    const run = (ms) => {
        startedAt = performance.now();
        remaining = ms;
        progressBar.style.transition = 'none';
        void progressBar.offsetWidth;  // force reflow so the reset above takes effect
        progressBar.style.transition = `width ${ms}ms linear`;
        progressBar.style.width = '0%';
        timerId = setTimeout(() => dismissToast(toastEl), ms);
    };

    const pause = () => {
        if (timerId === null) return;
        clearTimeout(timerId);
        timerId = null;
        remaining = Math.max(0, remaining - (performance.now() - startedAt));
        progressBar.style.transition = 'none';
        progressBar.style.width = getComputedStyle(progressBar).width;
    };

    const resume = () => {
        if (remaining <= 0) {
            dismissToast(toastEl);
            return;
        }
        void progressBar.offsetWidth;
        run(remaining);
    };

    toastEl.addEventListener('mouseenter', pause);
    toastEl.addEventListener('mouseleave', resume);
    requestAnimationFrame(() => run(initialDuration));
}

function dismissToast(toastEl) {
    if (!toastEl || !toastEl.isConnected || toastEl.classList.contains('leaving')) return;
    toastEl.classList.add('leaving');
    // Fallback in case animationend doesn't fire (e.g. toast removed some other way).
    const remove = () => toastEl.remove();
    toastEl.addEventListener('animationend', remove, { once: true });
    setTimeout(remove, 400);
}

// ==================== Loading Overlay ====================

// Safety-net timeout for the loading overlay: if the socket event we're
// waiting on never arrives (e.g. dropped connection), don't leave the user
// stuck behind the overlay forever.
const LOADING_OVERLAY_TIMEOUT_MS = 20000;
let loadingOverlayTimeoutId = null;

/**
 * Show the full-page loading overlay.
 * @param {string} [headline] - Optional bold headline text.
 * @param {string} [message] - Optional secondary/sub text.
 */
function showLoadingOverlay(headline, message) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;

    const headlineEl = document.getElementById('loading-headline');
    if (headlineEl) {
        headlineEl.textContent = headline || '';
        headlineEl.style.display = headline ? '' : 'none';
    }

    const messageEl = document.getElementById('loading-message');
    if (messageEl) {
        messageEl.textContent = message || '';
        messageEl.style.display = message ? '' : 'none';
    }

    overlay.style.display = 'flex';
}

function hideLoadingOverlay() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.style.display = 'none';
    if (loadingOverlayTimeoutId) {
        clearTimeout(loadingOverlayTimeoutId);
        loadingOverlayTimeoutId = null;
    }
}

/**
 * Hide the loading overlay as soon as the given one-off Socket.IO event is
 * received, or after LOADING_OVERLAY_TIMEOUT_MS - whichever comes first.
 * @param {string} eventName
 * @param {() => void} [onDone] - called right after the overlay is hidden.
 */
function waitForOverlayDismissal(eventName, onDone) {
    if (loadingOverlayTimeoutId) {
        clearTimeout(loadingOverlayTimeoutId);
    }
    const finish = () => {
        hideLoadingOverlay();
        if (onDone) onDone();
    };
    socket.once(eventName, finish);
    loadingOverlayTimeoutId = setTimeout(() => {
        socket.off(eventName, finish);
        finish();
    }, LOADING_OVERLAY_TIMEOUT_MS);
}

/**
 * Trigger a full campaign/inventory reload, showing the loading overlay
 * until it's done.
 * @param {{kind: 'campaign', campaignId: string, gameName: string} | {kind: 'auto_game', gameName: string} | null} [linkCheck] -
 *   context to verify once the refresh completes (e.g. from a "Refresh Status"
 *   button click), so we can warn the user if the account still isn't linked.
 */
async function reloadCampaigns(linkCheck = null) {
    state.pendingLinkCheck = linkCheck;
    const loadingText = state.translations.gui?.loading || {};
    showLoadingOverlay(
        loadingText.reload_headline || 'Refreshing...',
        loadingText.reload_message || 'Fetching your updated campaigns and link status...'
    );
    // unlinked_auto_items_update is broadcast once per full inventory refresh
    // (right after campaigns/wanted-items are recomputed), making it a
    // reliable "the reload is done" signal.
    waitForOverlayDismissal('unlinked_auto_items_update', checkPendingLinkStatus);
    try {
        await fetch('/api/reload', { method: 'POST' });
        // Status will update via Socket.IO when backend starts operation
    } catch (error) {
        console.error('Failed to reload:', error);
        hideLoadingOverlay();
        state.pendingLinkCheck = null;
    }
}

/**
 * After a reload triggered by a "Refresh Status" click, check whether the
 * account actually got linked. If not, revert the button back to "Link
 * Account" and warn the user via a toast.
 */
function checkPendingLinkStatus() {
    const pending = state.pendingLinkCheck;
    state.pendingLinkCheck = null;
    if (!pending) return;

    const toastText = state.translations.gui?.toasts || {};
    let linked = null;  // null = couldn't determine (e.g. campaign no longer present)

    if (pending.kind === 'campaign') {
        const campaign = state.campaigns[pending.campaignId];
        if (campaign) linked = !!campaign.linked;
    } else if (pending.kind === 'auto_game') {
        const stillUnlinked = state.unlinkedAutoItems.some(entry => entry.game_name === pending.gameName);
        linked = !stillUnlinked;
    }

    if (linked === false) {
        if (pending.kind === 'campaign') {
            state.linkClickedCampaigns.delete(pending.campaignId);
            renderInventory();
        } else if (pending.kind === 'auto_game') {
            state.linkClickedAutoGames.delete(pending.gameName);
            renderUnlinkedAutoItems(state.unlinkedAutoItems);
        }
        showToast(
            'warning',
            toastText.link_failed_headline || 'Account Link Failed',
            (toastText.link_failed_message || "{game} still isn't linked. Please try linking again.").replace('{game}', pending.gameName)
        );
    } else if (linked === true) {
        showToast(
            'success',
            toastText.link_success_headline || 'Account Linked',
            (toastText.link_success_message || '{game} is now linked. Happy mining!').replace('{game}', pending.gameName)
        );
    }
}


// ==================== Tab Management ====================

function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
}

// ==================== Event Listeners ====================

document.addEventListener('DOMContentLoaded', () => {
    // Fetch and display version information
    fetchAndDisplayVersion();

    // Tab switching
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', () => {
            switchTab(button.dataset.tab);
        });
    });

    // Login form
    document.getElementById('login-button').addEventListener('click', submitLogin);
    document.getElementById('oauth-confirm').addEventListener('click', confirmOAuth);

    // Settings - auto-save on change
    document.getElementById('dark-mode-auto').addEventListener('change', () => {
        applyDarkMode('auto');
        saveSettings();
    });
    document.getElementById('dark-mode-on').addEventListener('change', () => {
        applyDarkMode('on');
        saveSettings();
    });
    document.getElementById('dark-mode-off').addEventListener('change', () => {
        applyDarkMode('off');
        saveSettings();
    });
    document.getElementById('language')?.addEventListener('change', saveSettings);
    document.getElementById('animations-auto').addEventListener('change', () => {
        applyAnimationsMode('auto');
        saveSettings();
    });
    document.getElementById('animations-on').addEventListener('change', () => {
        applyAnimationsMode('on');
        saveSettings();
    });
    document.getElementById('animations-off').addEventListener('change', () => {
        applyAnimationsMode('off');
        saveSettings();
    });
    const connectionQualitySlider = document.getElementById('connection-quality');
    connectionQualitySlider.addEventListener('input', (e) => updateSliderVisual(e.target));
    connectionQualitySlider.addEventListener('change', saveSettings);
    updateSliderVisual(connectionQualitySlider);
    const refreshIntervalSlider = document.getElementById('minimum-refresh-interval');
    refreshIntervalSlider.addEventListener('input', (e) => updateSliderVisual(e.target));
    refreshIntervalSlider.addEventListener('change', saveSettings);
    updateSliderVisual(refreshIntervalSlider);
    // Proxy uses a manual "Set Proxy" button instead of auto-save
    document.getElementById('set-proxy-btn').addEventListener('click', () => {
        const proxyInput = document.getElementById('proxy-url');
        const newValue = proxyInput ? proxyInput.value : '';

        // Only save if changed
        if (newValue !== (state.settings.proxy || '')) {
            state.settings.proxy = newValue;
            saveSettings();
        }
    });
    document.getElementById('verify-proxy-btn').addEventListener('click', verifyProxy);
    document.getElementById('reload-btn').addEventListener('click', () => reloadCampaigns());
    document.getElementById('idle-mine-all-when-idle').addEventListener('change', saveSettings);


    // Games to watch management
    document.getElementById('select-all-btn').addEventListener('click', selectAllGames);
    document.getElementById('deselect-all-btn').addEventListener('click', deselectAllGames);
    document.getElementById('add-game-btn').addEventListener('click', addGameFromSearch);
    document.getElementById('games-filter').addEventListener('input', renderGamesToWatch);
    setupGamesDragAndDrop();

    // Inventory filters
    document.getElementById('filter-active').addEventListener('change', onInventoryStatusFilterChange);
    document.getElementById('filter-not-linked').addEventListener('change', onInventoryStatusFilterChange);
    document.getElementById('filter-upcoming').addEventListener('change', onInventoryStatusFilterChange);
    document.getElementById('filter-expired').addEventListener('change', onInventoryStatusFilterChange);
    document.getElementById('filter-finished').addEventListener('change', onInventoryStatusFilterChange);
    // Benefit type filters
    document.getElementById('filter-benefit-item').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-badge').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-emote').addEventListener('change', onInventoryFilterChange);
    document.getElementById('filter-benefit-other').addEventListener('change', onInventoryFilterChange);
    document.getElementById('inventory-view-mode-game').addEventListener('change', onInventoryFilterChange);
    document.getElementById('inventory-view-mode-category').addEventListener('change', onInventoryFilterChange);
    document.getElementById('clear-filters-btn').addEventListener('click', clearInventoryFilters);

    // Mining benefit settings
    document.getElementById('mining-benefit-item').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-badge').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-emote').addEventListener('change', saveSettings);
    document.getElementById('mining-benefit-unknown').addEventListener('change', saveSettings);

    // Game library sync
    document.getElementById('library-sync-enabled').addEventListener('change', () => {
        updateLibraryOptionsVisibility();
        saveSettings();
    });
    document.getElementById('steam-sync-enabled').addEventListener('change', saveSettings);
    document.getElementById('steam-api-key').addEventListener('change', saveSettings);
    document.getElementById('steam-id').addEventListener('change', saveSettings);
    document.getElementById('ubisoft-sync-enabled').addEventListener('change', saveSettings);
    document.getElementById('ubisoft-ticket').addEventListener('change', saveSettings);
    document.getElementById('library-mode-blacklist').addEventListener('change', onLibraryModeChange);
    document.getElementById('library-mode-whitelist').addEventListener('change', onLibraryModeChange);
    document.getElementById('library-game-search').addEventListener('input', renderLibraryOwnedList);
    document.getElementById('library-sync-now-btn').addEventListener('click', syncLibraryNow);

    // Load library data for the picker and provider status
    fetchOwnedGames();
    fetchLibraryStatus();


    // Inventory game search dropdown
    const gameSearchInput = document.getElementById('inventory-game-search');
    gameSearchInput.addEventListener('focus', () => {
        showGameDropdown();
    });
    gameSearchInput.addEventListener('input', (e) => {
        renderGameDropdown(e.target.value);
    });
    gameSearchInput.addEventListener('keydown', handleGameSearchKeydown);

    // Click outside to close dropdown
    document.addEventListener('click', (e) => {
        const container = document.querySelector('.game-dropdown-container');
        if (container && !container.contains(e.target) && gameDropdownVisible) {
            closeGameDropdown();
        }
    });

    // Manual mode controls
    const exitManualBtn = document.getElementById('exit-manual-btn');
    if (exitManualBtn) {
        exitManualBtn.addEventListener('click', exitManualMode);
    }

    // Fetch and populate available languages
    fetchAndPopulateLanguages();

    // Fetch and apply translations for the current language
    fetchAndApplyTranslations();

    // Request notification permission
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
});


// ==================== Wanted Items Rendering ====================

function renderWantedItems(tree) {
    const container = document.getElementById('wanted-items-list');
    if (!container) return;

    container.innerHTML = '';

    if (!tree || tree.length === 0) {
        const emptyMsg = state.translations.gui?.wanted?.none || 'No drops queued...';
        container.replaceChildren(makeElement('p', { class: 'empty-message-small' }, emptyMsg));
        return;
    }

    tree.forEach((gameGroup, index) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'wanted-game-group';

        // Game Icon
        let iconUrl = gameGroup.game_icon;
        if (iconUrl) {
            iconUrl = iconUrl.replace('{width}', '40').replace('{height}', '53');
        }

        const headerChildren = [makeElement('span', { class: 'wanted-game-index' }, `#${index + 1}`)];
        if (iconUrl) {
            headerChildren.push(makeImageElement(iconUrl, gameGroup.game_name, 'wanted-game-icon'));
        }
        headerChildren.push(makeElement('span', { class: 'wanted-game-title' }, gameGroup.game_name));
        if (gameGroup.source) {
            const sourceLabels = state.translations.gui?.wanted?.source || {};
            const sourceLabel = sourceLabels[gameGroup.source] || gameGroup.source;
            headerChildren.push(makeElement(
                'span',
                { class: `wanted-source-badge ${gameGroup.source}`, title: sourceLabel },
                sourceLabel
            ));
        }

        const headerEl = makeElement('div', { class: 'wanted-game-header' }, '', el => {
            headerChildren.forEach(child => el.appendChild(child));
        });
        groupEl.appendChild(headerEl);

        const campaignListEl = document.createElement('div');
        campaignListEl.className = 'wanted-campaign-list';

        gameGroup.campaigns.forEach(campaign => {
            const dropContainer = makeElement('div', {});
            const cardEl = makeElement('div', { class: 'wanted-card' }, '', el => {
                el.appendChild(makeElement('div', { class: 'wanted-card-header' }, '', h =>
                    h.appendChild(makeElement('a', { href: campaign.url, target: '_blank', rel: 'noopener noreferrer', class: 'wanted-card-campaign-link', title: campaign.name }, campaign.name))
                ));
                el.appendChild(makeElement('div', { class: 'wanted-card-body' }, '', b =>
                    b.appendChild(dropContainer)
                ));
            });

            campaign.drops.forEach(drop => {
                const dropEl = makeElement('div', { class: 'wanted-drop-item' }, '', el => {
                    el.appendChild(makeElement('span', { class: 'wanted-drop-name' }, drop.name));
                    drop.benefits.forEach(benefit => {
                        el.appendChild(makeElement('span', { class: 'wanted-benefit-pill' }, benefit));
                    });
                });
                dropContainer.appendChild(dropEl);
            });

            campaignListEl.appendChild(cardEl);
        });

        groupEl.appendChild(campaignListEl);
        container.appendChild(groupEl);
    });
}

function renderUnlinkedAutoItems(tree) {
    const container = document.getElementById('unlinked-auto-items-list');
    if (!container) return;

    container.innerHTML = '';

    const t = state.translations;
    state.unlinkedAutoItems = tree || [];
    if (!tree || tree.length === 0) {
        const emptyMsg = t.gui?.wanted?.unlinked_auto?.none || "No games awaiting link. Everything's linked!";
        container.replaceChildren(makeElement('p', { class: 'empty-message-small' }, emptyMsg));
        return;
    }

    const linkText = t.gui?.wanted?.unlinked_auto?.link_button || 'Link Account';
    const refreshText = t.gui?.wanted?.unlinked_auto?.refresh_button || 'Refresh Status';

    tree.forEach((gameGroup) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'wanted-game-group';

        // Game Icon
        let iconUrl = gameGroup.game_icon;
        if (iconUrl) {
            iconUrl = iconUrl.replace('{width}', '40').replace('{height}', '53');
        }

        const headerChildren = [];
        if (iconUrl) {
            headerChildren.push(makeImageElement(iconUrl, gameGroup.game_name, 'wanted-game-icon'));
        }
        headerChildren.push(makeElement('span', { class: 'wanted-game-title' }, gameGroup.game_name));
        if (gameGroup.source) {
            const sourceLabels = t.gui?.wanted?.source || {};
            const sourceLabel = sourceLabels[gameGroup.source] || gameGroup.source;
            headerChildren.push(makeElement(
                'span',
                { class: `wanted-source-badge ${gameGroup.source}`, title: sourceLabel },
                sourceLabel
            ));
        }

        const headerEl = makeElement('div', { class: 'wanted-game-header' }, '', el => {
            headerChildren.forEach(child => el.appendChild(child));
        });
        groupEl.appendChild(headerEl);

        // Single Link Account button per game (covers every unlinked campaign for it)
        const gameKey = gameGroup.game_name;
        const linkUrl = gameGroup.campaigns[0]?.link_url;
        if (linkUrl) {
            const actionsEl = makeElement('div', { class: 'wanted-game-actions' }, '', el => {
                el.appendChild(makeElement('button', { class: 'link-account-btn' }, linkText, btn => {
                    btn.addEventListener('click', () => {
                        window.open(linkUrl, '_blank');
                        if (!state.linkClickedAutoGames.has(gameKey)) {
                            state.linkClickedAutoGames.add(gameKey);
                            renderUnlinkedAutoItems(tree);
                        }
                    });
                }));
                if (state.linkClickedAutoGames.has(gameKey)) {
                    el.appendChild(makeElement('button', { class: 'link-account-btn refresh-status-btn' }, refreshText, btn => {
                        btn.addEventListener('click', () => reloadCampaigns({ kind: 'auto_game', gameName: gameKey }));
                    }));
                }
            });
            groupEl.appendChild(actionsEl);
        }

        const campaignListEl = document.createElement('div');
        campaignListEl.className = 'wanted-campaign-list';

        gameGroup.campaigns.forEach(campaign => {
            const dropContainer = makeElement('div', {});
            const cardEl = makeElement('div', { class: 'wanted-card' }, '', el => {
                el.appendChild(makeElement('div', { class: 'wanted-card-header' }, '', h =>
                    h.appendChild(makeElement('a', { href: campaign.url, target: '_blank', rel: 'noopener noreferrer', class: 'wanted-card-campaign-link', title: campaign.name }, campaign.name))
                ));
                el.appendChild(makeElement('div', { class: 'wanted-card-body' }, '', b => {
                    b.appendChild(dropContainer);
                }));
            });

            campaign.drops.forEach(drop => {
                const dropEl = makeElement('div', { class: 'wanted-drop-item' }, '', el => {
                    el.appendChild(makeElement('span', { class: 'wanted-drop-name' }, drop.name));
                    drop.benefits.forEach(benefit => {
                        el.appendChild(makeElement('span', { class: 'wanted-benefit-pill' }, benefit));
                    });
                });
                dropContainer.appendChild(dropEl);
            });

            campaignListEl.appendChild(cardEl);
        });

        groupEl.appendChild(campaignListEl);
        container.appendChild(groupEl);
    });
}

// ==================== DOM Utilities ====================

/**
 * @param {string} tag
 * @param {Record<string, string|number|boolean>} attrs
 * @param {string|number|null} text
 * @param {(el: HTMLElement) => void|null} callback
 */
function makeElement(tag, attrs = {}, text = null, callback = null) {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
    if (text !== null && text !== undefined) {
        el.textContent = String(text);
    }
    if (callback) {
        callback(el);
    }
    return el;
}

function makeImageElement(src, alt, className) {
    const image = makeElement('img', { src, alt, class: className });
    image.onerror = () => {
        image.style.display = 'none';
    };
    return image;
}

