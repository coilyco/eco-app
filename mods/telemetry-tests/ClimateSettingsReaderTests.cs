// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using Xunit;

// COI-3216: the reader looks members up by string, so a wrong name leaves a field null and the
// eco-app card quietly shows the Eco default. This object carries the member names and values
// of Configs/EcoSim.eco "ClimateSettings" as the Sirens server runs them.
public class ClimateSettingsReaderTests
{
    private sealed class EcoSimClimateSettings
    {
        public double MinCO2ppm { get; set; } = 325;
        public double TemperaturesRiseAtCO2ppm { get; set; } = 340;
        public double CO2ppmPerDegreeTemperatureRise { get; set; } = 20;
        public double SeaLevelsRiseAtCO2ppm { get; set; } = 420;
        public double CO2ppmPerSeaLevelMeterRise { get; set; } = 20;
        public double PollutionMultiplier { get; set; } = 0.5;
        public double MaxCO2PerDayFromAnimals { get; set; } = 25;
        public double MinCO2PerDayFromPlants { get; set; } = -500;
        public double MaxSeaLevelRise { get; set; } = 3;
        public double MaxSeaLevelRiseRatePerDay { get; set; } = 1;
        public double MaxTemperatureRise { get; set; } = 5;
        public double MaxTemperatureChangePerDay { get; set; } = 0.5;
    }

    [Fact]
    public void Every_field_reads_the_value_the_EcoSim_config_holds()
    {
        var dto = ClimateSettingsReader.From(new EcoSimClimateSettings());

        Assert.Equal(325, dto.MinCo2Ppm);
        Assert.Equal(340, dto.TemperatureThresholdPpm);
        Assert.Equal(20, dto.Co2PpmPerDegree);
        Assert.Equal(420, dto.SeaLevelThresholdPpm);
        Assert.Equal(20, dto.Co2PpmPerMeter);
        Assert.Equal(0.5, dto.PollutionMultiplier);
        Assert.Equal(25, dto.MaxCo2PerDayFromAnimals);
        Assert.Equal(-500, dto.MinCo2PerDayFromPlants);
        Assert.Equal(3, dto.MaxSeaLevelRise);
        Assert.Equal(1, dto.MaxSeaLevelRiseRatePerDay);
        Assert.Equal(5, dto.MaxTemperatureRise);
        Assert.Equal(0.5, dto.MaxTemperatureChangePerDay);
    }

    [Fact]
    public void A_member_the_object_lacks_stays_null_and_the_rest_still_read()
    {
        var dto = ClimateSettingsReader.From(new { SeaLevelsRiseAtCO2ppm = 420.0 });

        Assert.Equal(420, dto.SeaLevelThresholdPpm);
        Assert.Null(dto.MaxSeaLevelRise);
        Assert.Null(dto.MinCo2Ppm);
    }
}
