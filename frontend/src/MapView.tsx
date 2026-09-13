import { useEffect, useRef } from "react";
import { Map, Marker, NavigationControl } from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Snapshot } from "./types";

type Props = {
  snapshot: Snapshot;
  onSelectAgent: (agentId: string) => void;
};

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const SOURCE_IDS = [
  "agent-layer",
  "survivor-layer",
  "hazard-layer",
  "blocked-road-layer",
  "rescue-target-layer",
  "route-layer",
  "explored-area-layer",
  "blockage-layer",
];

export function MapView({ snapshot, onSelectAgent }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const markersRef = useRef<Marker[]>([]);

  function syncPointMarkers(map: Map, geojson: Snapshot["geojson"]) {
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = [];
    const layers = [
      ["agent-layer", "#52f2c2"],
      ["hazard-layer", "#ff7a45"],
      ["survivor-layer", "#f7f3e8"],
      ["blockage-layer", "#ff4d45"],
    ] as const;
    layers.forEach(([layerId, color]) => {
      geojson[layerId]?.features.forEach((feature) => {
        if (feature.geometry.type !== "Point") return;
        const isSurvivor = layerId === "survivor-layer";
        if (isSurvivor && feature.properties?.status === "rescued") return;
        const isTrapped = isSurvivor && feature.properties?.trapped === true;
        const markerColor = isTrapped ? "#b68cff" : color;
        const markerElement = document.createElement("div");
        markerElement.className = `map-marker ${layerId === "agent-layer" ? "agent-marker" : ""}`;
        markerElement.style.backgroundColor = markerColor;
        markerElement.title = isTrapped
          ? "Buried survivor"
          : isSurvivor
            ? "Visible injured survivor"
            : layerId;
        const marker = new Marker({ element: markerElement })
          .setLngLat(feature.geometry.coordinates as [number, number])
          .addTo(map);
        markerElement.style.zIndex = layerId === "agent-layer" ? "10" : "1";
        markersRef.current.push(marker);
      });
    });
  }

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new Map({
      container: container.current,
      style: {
        version: 8,
        sources: {
          openstreetmap: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 19,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          {
            id: "openstreetmap-basemap",
            type: "raster",
            source: "openstreetmap",
          },
        ],
      },
      center: [snapshot.world.center.lon, snapshot.world.center.lat],
      zoom: 14.2,
      pitch: 28,
      attributionControl: {},
    });
    map.addControl(new NavigationControl(), "bottom-right");
    map.on("load", () => {
      SOURCE_IDS.forEach((id) => {
        map.addSource(id, { type: "geojson", data: snapshot.geojson[id] ?? EMPTY });
      });
      map.resize();
      Object.entries(snapshot.geojson).forEach(([id, data]) => {
        (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
      });
      syncPointMarkers(map, snapshot.geojson);
      map.addLayer({
        id: "blocked-road-lines",
        type: "line",
        source: "blocked-road-layer",
        paint: { "line-color": "#ff4d45", "line-width": 7, "line-opacity": 0.85 },
      });
      map.addLayer({
        id: "blockage-points",
        type: "circle",
        source: "blockage-layer",
        paint: {
          "circle-radius": 10,
          "circle-color": "#ff4d45",
          "circle-opacity": 0.85,
          "circle-stroke-color": "#a8201a",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "route-lines",
        type: "line",
        source: "route-layer",
        paint: { "line-color": "#52f2c2", "line-width": 5, "line-dasharray": [1.5, 1] },
      });
      map.addLayer({
        id: "hazard-circles",
        type: "circle",
        source: "hazard-layer",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "intensity"], 0, 13, 1, 26],
          "circle-color": "#ff7a45",
          "circle-opacity": 0.38,
          "circle-stroke-color": "#ffbd80",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "target-rings",
        type: "circle",
        source: "rescue-target-layer",
        paint: {
          "circle-radius": 17,
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-color": "#f5d45c",
          "circle-stroke-width": 4,
        },
      });
      map.addLayer({
        id: "survivor-points",
        type: "circle",
        source: "survivor-layer",
        paint: {
          "circle-radius": ["case", ["get", "trapped"], 9, 7],
          "circle-color": [
            "match", ["get", "status"], "rescued", "#92a5a0", "assigned", "#f5d45c", "#f7f3e8",
          ],
          "circle-stroke-color": "#071310",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "agent-points",
        type: "circle",
        source: "agent-layer",
        paint: {
          "circle-radius": 10,
          "circle-color": [
            "match", ["get", "role"], "locate", "#5ca9ff", "coordinate", "#b68cff",
            "control", "#ff6e74", "priority", "#f4d35e", "#42e2b8",
          ],
          "circle-stroke-color": "#071310",
          "circle-stroke-width": 3,
        },
      });
      map.on("click", "agent-points", (event: MapLayerMouseEvent) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === "string") onSelectAgent(id);
      });
      map.on("mouseenter", "agent-points", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "agent-points", () => (map.getCanvas().style.cursor = ""));
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markersRef.current = [];
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    Object.entries(snapshot.geojson).forEach(([id, data]) => {
      (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
    });
    syncPointMarkers(map, snapshot.geojson);
  }, [snapshot]);

  return <div className="map" ref={container} aria-label="Earthquake response map" />;
}
