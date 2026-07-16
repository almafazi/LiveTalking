import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Mic, MicOff, RefreshCw, Send, Volume2, VolumeX } from 'lucide-react';
import mpegts from 'mpegts.js';
import { ConversationClient } from './conversation-client.js';

const labels = {
    ms: { idle: 'Ketuk untuk bercakap', connecting: 'Menyediakan suara…', listening: 'Saya sedang mendengar…', thinking: 'Berfikir…', speaking: 'Bercakap…', placeholder: 'Bercakap atau taip soalan anda…' },
    en: { idle: 'Tap to talk', connecting: 'Preparing voice…', listening: 'I am listening…', thinking: 'Thinking…', speaking: 'Speaking…', placeholder: 'Speak or type your question…' },
};

function InteractiveExperience() {
    const videoRef = useRef(null);
    const playerRef = useRef(null);
    const conversationRef = useRef(null);
    const [config, setConfig] = useState(null);
    const [language, setLanguage] = useState('ms');
    const [voiceState, setVoiceState] = useState('idle');
    const [muted, setMuted] = useState(false);
    const [draft, setDraft] = useState('');
    const [error, setError] = useState('');

    async function loadConfig() {
        const response = await fetch('/api/public/config', { cache: 'no-store' });
        const payload = await response.json();
        setConfig(payload.data);
    }

    useEffect(() => { loadConfig().catch((e) => setError(e.message)); }, []);

    useEffect(() => {
        if (!config || config.maintenance) return undefined;
        let cancelled = false;
        let peer;
        let fallback;

        async function connectStream() {
            try {
                peer = new RTCPeerConnection();
                peer.addTransceiver('audio', { direction: 'recvonly' });
                peer.addTransceiver('video', { direction: 'recvonly' });
                peer.ontrack = (event) => {
                    if (videoRef.current) videoRef.current.srcObject = event.streams[0];
                };
                const offer = await peer.createOffer();
                await peer.setLocalDescription(offer);
                const response = await fetch(config.media.whep_url, {
                    method: 'POST', headers: { 'Content-Type': 'application/sdp' }, body: offer.sdp,
                });
                if (!response.ok) throw new Error(`WHEP ${response.status}`);
                await peer.setRemoteDescription({ type: 'answer', sdp: await response.text() });
            } catch (whepError) {
                if (cancelled || !mpegts.isSupported()) throw whepError;
                fallback = mpegts.createPlayer({ type: 'flv', isLive: true, url: config.media.flv_url });
                fallback.attachMediaElement(videoRef.current);
                fallback.load();
                await fallback.play();
            }
        }

        connectStream().catch((e) => setError(`Stream tidak tersedia: ${e.message}`));
        return () => {
            cancelled = true;
            peer?.close();
            fallback?.destroy();
        };
    }, [config?.revision, config?.maintenance]);

    useEffect(() => {
        if (videoRef.current) videoRef.current.muted = muted;
    }, [muted]);

    useEffect(() => () => conversationRef.current?.stop(), []);

    // The stream is pillarboxed by object-fit: contain, so the CSS edge fade must
    // start where the video content actually ends, not at the element border.
    function updateEdgeFade() {
        const video = videoRef.current;
        if (!video || !video.videoWidth || !video.videoHeight) return;
        const rect = video.getBoundingClientRect();
        const contentWidth = Math.min(rect.width, rect.height * (video.videoWidth / video.videoHeight));
        video.style.setProperty('--edge', `${Math.max(0, (rect.width - contentWidth) / 2)}px`);
    }

    useEffect(() => {
        window.addEventListener('resize', updateEdgeFade);
        return () => window.removeEventListener('resize', updateEdgeFade);
    }, []);

    async function toggleConversation() {
        if (conversationRef.current) {
            conversationRef.current.stop();
            conversationRef.current = null;
            setVoiceState('idle');
            return;
        }

        setError('');
        setVoiceState('connecting');
        try {
            const response = await fetch('/api/public/conversation', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content },
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.message || 'Conversation bootstrap failed.');
            const client = new ConversationClient({
                ...payload.data,
                language,
                onState: setVoiceState,
                onError: (message) => setError(message),
            });
            conversationRef.current = client;
            await client.start();
        } catch (e) {
            conversationRef.current = null;
            setVoiceState('idle');
            setError(e.message);
        }
    }

    function sendPrompt(event) {
        event.preventDefault();
        const text = draft.trim();
        if (!text) return;
        if (!conversationRef.current) {
            setError('Aktifkan mikrofon terlebih dahulu.');
            return;
        }
        conversationRef.current.sendText(text);
        setDraft('');
        setVoiceState('thinking');
    }

    if (!config) return <main className="loadingScreen">Loading experience…</main>;
    if (config.maintenance) return <main className="maintenanceScreen"><RefreshCw className="spin" /><h1>{config.brand_name}</h1><p>{config.maintenance_message || 'Maintenance in progress.'}</p><button onClick={() => window.location.reload()}>Refresh</button></main>;

    const activeLabels = labels[language] || labels.ms;
    return (
        <main className="experience" data-background={config.background} style={{ '--accent': config.accent_color }}>
            <div className="studio"><div className="floor" /></div>
            <video ref={videoRef} autoPlay playsInline muted={muted} className="avatarVideo" onLoadedMetadata={updateEdgeFade} onResize={updateEdgeFade} />
            <button className="roundButton mute" onClick={() => setMuted(!muted)} aria-label="Mute">{muted ? <VolumeX /> : <Volume2 />}</button>
            <button className="roundButton reload" onClick={() => window.location.reload()} aria-label="Reload"><RefreshCw /></button>
            <aside className="controls">
                <div className="languages">{(config.enabled_languages || ['ms', 'en']).map((code) => <button key={code} className={language === code ? 'active' : ''} onClick={() => { conversationRef.current?.stop(); conversationRef.current = null; setVoiceState('idle'); setLanguage(code); }}>{code.toUpperCase()}</button>)}</div>
                <button className={`micButton ${voiceState}`} onClick={toggleConversation}>{conversationRef.current ? <MicOff /> : <Mic />}</button>
                <p className="status">{activeLabels[voiceState] || activeLabels.idle}</p>
                <form className="prompt" onSubmit={sendPrompt}><input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={activeLabels.placeholder} /><button disabled={!draft.trim()}><Send /></button></form>
                {error && <p className="error">{error}</p>}
            </aside>
        </main>
    );
}

createRoot(document.getElementById('app')).render(<InteractiveExperience />);
