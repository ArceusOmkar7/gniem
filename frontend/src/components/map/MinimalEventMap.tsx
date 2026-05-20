import React, { useMemo, useRef, useState, useCallback } from 'react';
import Map, { Layer, Source } from 'react-map-gl/mapbox';
import type { MapRef } from 'react-map-gl/mapbox';
import type { Feature, FeatureCollection, Point } from 'geojson';
import { useQuery } from '@tanstack/react-query';
import { useStore } from '../../store/useStore';
import { apiService } from '../../services/api';
import type { MapAggregation } from '../../types';

const MAPBOX_ACCESS_TOKEN = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;
const HAS_MAPBOX_TOKEN =
  typeof MAPBOX_ACCESS_TOKEN === 'string' && MAPBOX_ACCESS_TOKEN.trim().length > 0;

export const MinimalEventMap: React.FC<{ themeCategory?: string | null }> = ({ themeCategory }) => {
  const {
    viewState,
    setViewState,
    dateRange,
    eventRootCodes,
    geoFilter,
    dateWindowReady,
    isDarkTheme,
  } = useStore();

  const mapRef = useRef<MapRef>(null);
  const [mapBBox, setMapBBox] = useState({ n: 90, s: -90, e: 180, w: -180 });

  const updateBBox = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const bounds = map.getBounds();
    if (!bounds) return;
    setMapBBox({
      w: bounds.getWest(),
      s: bounds.getSouth(),
      e: bounds.getEast(),
      n: bounds.getNorth(),
    });
  }, []);

  const { data: mapResponse } = useQuery({
    queryKey: ['minimal-map-data', mapBBox, viewState.zoom, dateRange, eventRootCodes, geoFilter, themeCategory],
    queryFn: () => apiService.getMapData(
      mapBBox,
      viewState.zoom,
      dateRange[0],
      dateRange[1],
      eventRootCodes,
      geoFilter,
      themeCategory
    ),
    enabled: dateWindowReady,
    staleTime: 60000,
  });

  const geoJson = useMemo<FeatureCollection<Point, any> | null>(() => {
    if (!mapResponse?.data || mapResponse.count === 0) return null;
    return {
      type: 'FeatureCollection',
      features: (mapResponse.data as any[]).map(
        (d): Feature<Point, any> => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [d.lon ?? d.ActionGeo_Long, d.lat ?? d.ActionGeo_Lat] },
          properties: { ...d },
        })
      ),
    };
  }, [mapResponse]);

  if (!HAS_MAPBOX_TOKEN) return <div className="w-full h-full bg-surface-900" />;

  return (
    <div className="w-full h-full grayscale-[0.2] opacity-90 transition-opacity duration-500">
      <Map
        ref={mapRef}
        {...viewState}
        projection="mercator"
        pitch={0}
        bearing={0}
        dragRotate={false}
        touchZoomRotate={false}
        onMove={evt => setViewState(evt.viewState)}
        onLoad={updateBBox}
        onMoveEnd={updateBBox}
        mapboxAccessToken={MAPBOX_ACCESS_TOKEN}
        mapStyle={isDarkTheme ? 'mapbox://styles/mapbox/dark-v11' : 'mapbox://styles/mapbox/light-v11'}
        style={{ width: '100%', height: '100%' }}
      >
        {geoJson && (
          <Source id="minimal-source" type="geojson" data={geoJson}>
            <Layer
              id="minimal-heat"
              type="heatmap"
              paint={{
                'heatmap-weight': [
                  'interpolate',
                  ['linear'],
                  ['ln', ['+', ['coalesce', ['get', 'intensity'], ['get', 'num_mentions'], 1], 1]],
                  0, 0,
                  10, 1
                ],
                'heatmap-intensity': [
                  'interpolate',
                  ['linear'],
                  ['zoom'],
                  0, 0.2,
                  3, 0.6,
                  6, 1.0,
                  9, 1.5
                ],
                'heatmap-radius': [
                  'interpolate',
                  ['linear'],
                  ['zoom'],
                  0, 4,
                  3, 10,
                  6, 18,
                  9, 25
                ],
                'heatmap-color': [
                  'interpolate',
                  ['linear'],
                  ['heatmap-density'],
                  0, 'rgba(0, 0, 0, 0)',
                  0.15, 'rgba(0, 243, 255, 0.2)',
                  0.4, 'rgba(0, 255, 65, 0.45)',
                  0.65, 'rgba(255, 210, 0, 0.65)',
                  0.85, 'rgba(255, 110, 0, 0.8)',
                  0.98, 'rgba(255, 0, 60, 0.95)'
                ],
                'heatmap-opacity': [
                  'interpolate',
                  ['linear'],
                  ['zoom'],
                  0, 0.75,
                  6, 0.6,
                  9, 0.4
                ]
              }}
            />
          </Source>
        )}
      </Map>
    </div>
  );
};
