using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Text;
using System.Threading.Tasks;
using MQTTnet;
using MQTTnet.Protocol;

namespace MQTT_Tools_IHM
{
    public class MqttService
    {
        private readonly IMqttClient _mqttClient;
        private readonly MqttClientOptions _options;
        private readonly ConcurrentDictionary<string, ConcurrentQueue<DataPoint>> _dataSeries;
        private readonly int _maxDataPoints = 1000;

        public event Action<string, double> OnNewDataPoint;
        public event Action<string, string> OnNewParameter;
        public event Action<string> OnVariableAdded;
        public event Action<string> OnVariableRemoved;
        public event Action<string> OnFilterAdded;
        public event Action<string> OnFilterRemoved;

        public MqttService(string brokerHost, int brokerPort)
        {
            _dataSeries = new ConcurrentDictionary<string, ConcurrentQueue<DataPoint>>();
            var factory = new MqttClientFactory();
            _mqttClient = factory.CreateMqttClient();
             _options = new MqttClientOptionsBuilder()
                .WithTcpServer(brokerHost, brokerPort)
                .WithCleanSession()
                .WithClientId(Guid.NewGuid().ToString()) // Important pour éviter les conflits
                .WithProtocolVersion(MQTTnet.Formatter.MqttProtocolVersion.V311) // Forcer la version 3.1.1
                .Build();

            _mqttClient.DisconnectedAsync += async e =>
            {
                Console.WriteLine("Déconnecté du broker MQTT.");
                await Task.Delay(TimeSpan.FromSeconds(5));
                try
                {
                    await _mqttClient.ConnectAsync(_options, CancellationToken.None);
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"Erreur de reconnexion: {ex.Message}");
                }
            };

        }

        public async Task ConnectAsync()
        {
            try
            {
                await _mqttClient.ConnectAsync(_options, CancellationToken.None);

                // Abonnements avec Qos=1 pour plus de fiabilité
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("simulateur/+/value")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("simulateur/+/value_filtered_#")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("simulateur/new")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("simulateur/delete")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("Filter/new")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("Filter/delete")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());
                await _mqttClient.SubscribeAsync(new MqttTopicFilterBuilder()
                    .WithTopic("simulateur/+/parameters/#")
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build());

                _mqttClient.ApplicationMessageReceivedAsync += async e =>
                {
                    await Task.Run(() =>
                    {
                        try
                        {
                            var topic = e.ApplicationMessage.Topic;
                            var payload = Encoding.UTF8.GetString(e.ApplicationMessage.Payload);
                            ProcessMessage(topic, payload);
                        }
                        catch (Exception ex)
                        {
                            Console.WriteLine($"Erreur dans ApplicationMessageReceivedAsync: {ex.Message}");
                        }
                    });
                };
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Erreur de connexion: {ex.Message}");
            }
        }


        private void ProcessMessage(string topic, string payload)
        {
            try
            {
                if (topic.StartsWith("simulateur/") && topic.EndsWith("/value"))
                {
                    var varName = topic.Split('/')[1];
                    if (double.TryParse(payload, out double value))
                    {
                        var seriesKey = $"{varName}_raw";
                        var dataPoint = new DataPoint { Timestamp = DateTime.Now, Value = value };
                        if (!_dataSeries.TryGetValue(seriesKey, out var queue))
                        {
                            queue = new ConcurrentQueue<DataPoint>();
                            _dataSeries[seriesKey] = queue;
                        }
                        queue.Enqueue(dataPoint);
                        if (queue.Count > _maxDataPoints)
                            queue.TryDequeue(out _);
                        OnNewDataPoint?.Invoke(seriesKey, value);
                    }
                }
                else if (topic.Contains("value_filtered_"))
                {
                    var parts = topic.Split('/');
                    var varName = parts[1];
                    var filterName = parts[2].Replace("value_filtered_", "");
                    var seriesKey = $"{varName}_filtered_{filterName}";
                    if (double.TryParse(payload, out double value))
                    {
                        var dataPoint = new DataPoint { Timestamp = DateTime.Now, Value = value };
                        if (!_dataSeries.TryGetValue(seriesKey, out var queue))
                        {
                            queue = new ConcurrentQueue<DataPoint>();
                            _dataSeries[seriesKey] = queue;
                        }
                        queue.Enqueue(dataPoint);
                        if (queue.Count > _maxDataPoints)
                            queue.TryDequeue(out _);
                        OnNewDataPoint?.Invoke(seriesKey, value);
                    }
                }
                else if (topic == "simulateur/new")
                {
                    OnVariableAdded?.Invoke(payload);
                }
                else if (topic == "simulateur/delete")
                {
                    OnVariableRemoved?.Invoke(payload);
                }
                else if (topic == "Filter/new")
                {
                    OnFilterAdded?.Invoke(payload);
                }
                else if (topic == "Filter/delete")
                {
                    OnFilterRemoved?.Invoke(payload);
                }
                else if (topic.Contains("/parameters/"))
                {
                    var parts = topic.Split('/');
                    var varName = parts[1];
                    var paramName = parts[3];
                    OnNewParameter?.Invoke($"{varName}_{paramName}", payload);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error processing message: {ex.Message}");
            }
        }

        public async Task PublishAsync(string topic, string payload, bool retain = false)
        {
            var message = new MqttApplicationMessageBuilder()
                .WithTopic(topic)
                .WithPayload(Encoding.UTF8.GetBytes(payload)) // Encoder explicitement en UTF-8
                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                .WithRetainFlag(retain)
                .Build();
            await _mqttClient.PublishAsync(message, CancellationToken.None);
        }

        public IEnumerable<DataPoint> GetDataPoints(string seriesKey)
        {
            if (_dataSeries.TryGetValue(seriesKey, out var queue))
                return queue;
            return new List<DataPoint>();
        }
    }
}
