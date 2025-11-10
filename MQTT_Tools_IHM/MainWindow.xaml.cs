using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;
using System.Windows.Shapes;
using LiveCharts;
using LiveCharts.Wpf;
using Newtonsoft.Json;

namespace MQTT_Tools_IHM
{
    public partial class MainWindow : Window
    {
        private readonly MqttService _mqttService;
        private readonly Dictionary<string, SeriesCollection> _seriesCollections;

        public MainWindow()
        {
            InitializeComponent();
            _mqttService = new MqttService("localhost", 1883);

            _seriesCollections = new Dictionary<string, SeriesCollection>
            {
                ["Variables"] = new SeriesCollection(),
                ["Filters"] = new SeriesCollection()
            };
            VariablesChart.Series = _seriesCollections["Variables"];
            FiltersChart.Series = _seriesCollections["Filters"];
            Loaded += async (s, e) => await InitializeAsync();
        }

        private async Task InitializeAsync()
        {
            try
            {
                await _mqttService.ConnectAsync();
                StatusTextBlock.Text = "Connecté au broker MQTT";
                _mqttService.OnNewDataPoint += OnNewDataPoint;
                _mqttService.OnVariableAdded += OnVariableAdded;
                _mqttService.OnVariableRemoved += OnVariableRemoved;
                _mqttService.OnFilterAdded += OnFilterAdded;
                _mqttService.OnFilterRemoved += OnFilterRemoved;
            }
            catch (Exception ex)
            {
                StatusTextBlock.Text = $"Erreur: {ex.Message}";
            }
        }

        private void OnNewDataPoint(string seriesKey, double value)
        {
            Dispatcher.Invoke(() =>
            {
                var chartType = seriesKey.Contains("_filtered_") ? "Filters" : "Variables";
                var series = _seriesCollections[chartType].FirstOrDefault(s => s.Title == seriesKey);
                if (series == null)
                {
                    series = new LineSeries
                    {
                        Title = seriesKey,
                        Values = new ChartValues<double>(),
                        PointGeometry = DefaultGeometries.Circle,
                        PointGeometrySize = 5,
                        StrokeThickness = 1
                    };
                    _seriesCollections[chartType].Add(series);
                }
                var values = (ChartValues<double>)series.Values;
                values.Add(value);
                if (values.Count > 1000)
                    values.RemoveAt(0);
            });
        }

        private void OnVariableAdded(string payload)
        {
            Dispatcher.Invoke(() =>
            {
                try
                {
                    var varInfo = JsonConvert.DeserializeObject<Dictionary<string, object>>(payload);
                    var name = varInfo["name"].ToString();
                    VariablesListBox.Items.Add(name);
                    FilterSourceComboBox.Items.Add(name);
                }
                catch { }
            });
        }

        private void OnVariableRemoved(string name)
        {
            Dispatcher.Invoke(() =>
            {
                VariablesListBox.Items.Remove(name);
                FilterSourceComboBox.Items.Remove(name);
                var series = _seriesCollections["Variables"].FirstOrDefault(s => s.Title.StartsWith($"{name}_"));
                if (series != null)
                    _seriesCollections["Variables"].Remove(series);
            });
        }

        private void OnFilterAdded(string sourceTopic)
        {
            Dispatcher.Invoke(() =>
            {
                var parts = sourceTopic.Split('/');
                var varName = parts[1];
                var filterName = $"{varName}_filtered_{parts[2].Replace("value_filtered_", "")}";
                FiltersListBox.Items.Add(filterName);
            });
        }

        private void OnFilterRemoved(string filterName)
        {
            Dispatcher.Invoke(() =>
            {
                FiltersListBox.Items.Remove(filterName);
                var series = _seriesCollections["Filters"].FirstOrDefault(s => s.Title == filterName);
                if (series != null)
                    _seriesCollections["Filters"].Remove(series);
            });
        }

        private async void AddVariable_Click(object sender, RoutedEventArgs e)
        {
            var name = VarNameTextBox.Text.Trim();
            if (string.IsNullOrEmpty(name))
                return;
            var payload = new
            {
                name,
                period = 60.0,
                min = 15.0,
                max = 61.0,
                noise = 5.0,
                period_publish = 0.5
            };
            await _mqttService.PublishAsync("simulateur/new", JsonConvert.SerializeObject(payload));
        }

        private async void DeleteVariable_Click(object sender, RoutedEventArgs e)
        {
            if (VariablesListBox.SelectedItem is string name)
                await _mqttService.PublishAsync("simulateur/delete", name);
        }

        private async void AddFilter_Click(object sender, RoutedEventArgs e)
        {
            if (FilterSourceComboBox.SelectedItem is string sourceTopic)
                await _mqttService.PublishAsync("Filter/new", $"simulateur/{sourceTopic}/value");
        }

        private async void DeleteFilter_Click(object sender, RoutedEventArgs e)
        {
            if (FiltersListBox.SelectedItem is string filterName)
                await _mqttService.PublishAsync("Filter/delete", filterName);
        }

        private void VariablesListBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (VariablesListBox.SelectedItem is string name)
            {
                PeriodTextBox.Text = _mqttService.GetDataPoints($"{name}_period").LastOrDefault()?.Value.ToString() ?? "60";
                MinTextBox.Text = _mqttService.GetDataPoints($"{name}_min").LastOrDefault()?.Value.ToString() ?? "15";
                MaxTextBox.Text = _mqttService.GetDataPoints($"{name}_max").LastOrDefault()?.Value.ToString() ?? "61";
                NoiseTextBox.Text = _mqttService.GetDataPoints($"{name}_noise").LastOrDefault()?.Value.ToString() ?? "5";
                PublishPeriodTextBox.Text = _mqttService.GetDataPoints($"{name}_period_publish").LastOrDefault()?.Value.ToString() ?? "0.5";
            }
        }

        private async void UpdateParameters_Click(object sender, RoutedEventArgs e)
        {
            if (VariablesListBox.SelectedItem is string name)
            {
                await _mqttService.PublishAsync($"simulateur/{name}/parameters/period", PeriodTextBox.Text, true);
                await _mqttService.PublishAsync($"simulateur/{name}/parameters/min", MinTextBox.Text, true);
                await _mqttService.PublishAsync($"simulateur/{name}/parameters/max", MaxTextBox.Text, true);
                await _mqttService.PublishAsync($"simulateur/{name}/parameters/noise", NoiseTextBox.Text, true);
                await _mqttService.PublishAsync($"simulateur/{name}/parameters/period_publish", PublishPeriodTextBox.Text, true);
            }
        }

        private void FiltersListBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            // TODO: Ajouter la gestion des paramètres de filtre si besoin
        }
    }
}
