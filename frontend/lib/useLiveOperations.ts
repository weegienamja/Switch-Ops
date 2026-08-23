"use client";

import { useCallback, useEffect, useState } from "react";
import { api, backendEventStreamUrl } from "./api";
import type {
  ConfigSaveState,
  LiveConnection,
  LiveFreshness,
  LiveInterfaceState,
  LiveSnapshot,
  LldpDiscoveryStatus,
  OperationKind,
  OperationProgress,
  OperationResult,
  WriteLockStatus,
  TopologyModel,
} from "./types";

type StreamState = "connecting" | "open" | "error" | "unavailable";

const OFFLINE: LiveConnection = { state: "offline", queueDepth: 0 };
const LOCKED: WriteLockStatus = { capability: false, unlocked: false };
const UNKNOWN_CONFIG: ConfigSaveState = {
  runningModified: false,
  pendingOperations: 0,
  detail: "Running and startup configuration have not been compared yet.",
};

interface EventEnvelope<T> {
  at: string;
  data: T;
}

function eventData<T>(event: Event): T | null {
  try {
    return (JSON.parse((event as MessageEvent<string>).data) as EventEnvelope<T>).data;
  } catch {
    return null;
  }
}

export function useLiveOperations(enabled: boolean) {
  const [interfaces, setInterfaces] = useState<LiveInterfaceState[]>([]);
  const [freshness, setFreshness] = useState<LiveFreshness>({});
  const [connection, setConnection] = useState<LiveConnection>(OFFLINE);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [operation, setOperation] = useState<OperationProgress | null>(null);
  const [lock, setLock] = useState<WriteLockStatus>(LOCKED);
  const [config, setConfig] = useState<ConfigSaveState>(UNKNOWN_CONFIG);
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const [lldp, setLldp] = useState<LldpDiscoveryStatus | null>(null);
  const [topology, setTopology] = useState<TopologyModel | null>(null);

  const applySnapshot = useCallback((snapshot: LiveSnapshot) => {
    setInterfaces(snapshot.interfaces || []);
    setFreshness(snapshot.freshness || {});
    setConnection(snapshot.connection || OFFLINE);
    if (snapshot.discovery?.lldp) setLldp(snapshot.discovery.lldp);
    if (snapshot.topology) setTopology(snapshot.topology);
    if (snapshot.operationInProgress) {
      const [kind, interfaceName] = snapshot.operationInProgress.split(":", 2);
      setOperation({
        kind: kind as OperationKind,
        interface: interfaceName || "",
        stages: [],
        status: "running",
      });
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    void Promise.allSettled([api.liveState(), api.controlLock(), api.refreshConfigState()]).then(
      ([liveResult, lockResult, configResult]) => {
        if (cancelled) return;
        if (liveResult.status === "fulfilled") applySnapshot(liveResult.value);
        if (lockResult.status === "fulfilled") setLock(lockResult.value);
        if (configResult.status === "fulfilled") setConfig(configResult.value);
      },
    );

    if (typeof EventSource === "undefined") {
      setStreamState("unavailable");
      return () => {
        cancelled = true;
      };
    }

    setStreamState("connecting");
    const source = new EventSource(backendEventStreamUrl());
    source.onopen = () => setStreamState("open");
    source.onerror = () => setStreamState("error");
    const listen = <T,>(name: string, apply: (payload: T) => void) => {
      source.addEventListener(name, (event) => {
        const payload = eventData<T>(event);
        if (payload == null) return;
        setLastEventAt(new Date().toISOString());
        apply(payload);
      });
    };
    listen<LiveSnapshot>("snapshot", applySnapshot);
    listen<{ interfaces: LiveInterfaceState[]; freshness: LiveFreshness }>(
      "interface_state",
      (payload) => {
        setInterfaces(payload.interfaces || []);
        setFreshness(payload.freshness || {});
      },
    );
    listen<{ interfaces: LiveInterfaceState[] }>("poe_state", (payload) =>
      setInterfaces(payload.interfaces || []),
    );
    listen<LiveFreshness>("freshness", setFreshness);
    listen<LiveConnection>("connection_state", setConnection);
    listen<OperationProgress>("operation_progress", setOperation);
    listen<OperationResult>("operation_complete", (result) =>
      setOperation({
        kind: result.kind,
        interface: result.interface,
        stages: result.stages,
        status: result.status,
        result,
      }),
    );
    listen<WriteLockStatus>("control_lock", setLock);
    listen<ConfigSaveState>("config_state", setConfig);
    listen<{ lldp: LldpDiscoveryStatus }>("discovery_state", (payload) =>
      setLldp(payload.lldp),
    );
    listen<TopologyModel>("topology_state", setTopology);

    return () => {
      cancelled = true;
      source.close();
    };
  }, [applySnapshot, enabled]);

  const unlock = useCallback(async () => {
    const next = await api.unlockControl();
    setLock(next);
    return next;
  }, []);

  const lockNow = useCallback(async () => {
    const next = await api.lockControl();
    setLock(next);
    return next;
  }, []);

  const runOperation = useCallback(
    async (port: string, kind: OperationKind, value?: string) => {
      setOperation({ kind, interface: port, stages: [], status: "running" });
      try {
        const result = await api.runOperation(port, kind, value);
        setOperation({
          kind,
          interface: port,
          stages: result.stages,
          status: result.status,
          result,
        });
        if (result.requiresSave) setConfig(await api.configState());
        return result;
      } catch (error) {
        setOperation(null);
        throw error;
      }
    },
    [],
  );

  const save = useCallback(async () => {
    const result = await api.saveConfig();
    setConfig(result.state);
    return result;
  }, []);

  const refreshConfig = useCallback(async () => {
    const next = await api.refreshConfigState();
    setConfig(next);
    return next;
  }, []);

  return {
    interfaces,
    freshness,
    connection,
    streamState,
    operation,
    lock,
    config,
    lastEventAt,
    lldp,
    topology,
    unlock,
    lockNow,
    runOperation,
    save,
    refreshConfig,
  };
}

export type LiveOperationsController = ReturnType<typeof useLiveOperations>;
