INSERT INTO public.system_configs (
  domain,
  config_key,
  environment,
  status,
  value_type,
  config_value,
  description
)
SELECT
  'profile',
  'avatar_presets',
  env.environment,
  1,
  5,
  $json$
{
  "enabled": true,
  "items": [
    { "id": "the_player", "src": "/assets/icons/The_Player_Icon.png", "label": "Player", "enabled": true, "order": 10 },
    { "id": "forester", "src": "/assets/icons/Forester.png", "label": "Forester", "enabled": true, "order": 20 },
    { "id": "junimo", "src": "/assets/icons/Junimo_Icon.png", "label": "Junimo", "enabled": true, "order": 30 },
    { "id": "smapi_mascot", "src": "/assets/icons/SMAPI_mascot.png", "label": "SMAPI", "enabled": true, "order": 40 },
    { "id": "marriage", "src": "/assets/icons/Marriage_Icon.png", "label": "Marriage", "enabled": true, "order": 50 },
    { "id": "multiplayer", "src": "/assets/icons/Multiplayer_Icon.png", "label": "Multiplayer", "enabled": true, "order": 60 },
    { "id": "achievements", "src": "/assets/icons/Achievements_Icon.png", "label": "Achievements", "enabled": true, "order": 70 },
    { "id": "bundle", "src": "/assets/icons/Bundle_Icon.png", "label": "Bundle", "enabled": true, "order": 80 },
    { "id": "quests", "src": "/assets/icons/Quests_Icon.png", "label": "Quests", "enabled": true, "order": 90 },
    { "id": "golden_walnut", "src": "/assets/icons/Golden_Walnut.png", "label": "Golden Walnut", "enabled": true, "order": 100 },
    { "id": "quality_sprinkler", "src": "/assets/icons/Quality_Sprinkler.png", "label": "Sprinkler", "enabled": true, "order": 110 },
    { "id": "health", "src": "/assets/icons/Health.png", "label": "Health", "enabled": true, "order": 120 },
    { "id": "energy", "src": "/assets/icons/Energy.png", "label": "Energy", "enabled": true, "order": 130 },
    { "id": "cinder_shard", "src": "/assets/icons/Cinder_Shard.png", "label": "Cinder Shard", "enabled": true, "order": 140 },
    { "id": "sweet_pea", "src": "/assets/icons/Sweet_Pea.png", "label": "Sweet Pea", "enabled": true, "order": 150 },
    { "id": "dandelion", "src": "/assets/icons/Dandelion.png", "label": "Dandelion", "enabled": true, "order": 160 },
    { "id": "apple_sapling", "src": "/assets/icons/Apple_Sapling.png", "label": "Apple Sapling", "enabled": true, "order": 170 },
    { "id": "corn", "src": "/assets/icons/Corn.png", "label": "Corn", "enabled": true, "order": 180 },
    { "id": "book_of_mysteries", "src": "/assets/icons/Book_of_Mysteries.png", "label": "Book", "enabled": true, "order": 190 },
    { "id": "shipping_box", "src": "/assets/icons/ShippingBox.png", "label": "Shipping Box", "enabled": true, "order": 200 }
  ]
}
$json$,
  'Preset user avatars available on the profile page.'
FROM (VALUES ('dev'), ('test'), ('prod')) AS env(environment)
ON CONFLICT (domain, config_key, environment)
DO UPDATE SET
  status = EXCLUDED.status,
  value_type = EXCLUDED.value_type,
  config_value = EXCLUDED.config_value,
  description = EXCLUDED.description;
