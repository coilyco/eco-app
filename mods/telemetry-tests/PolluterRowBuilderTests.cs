// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using Xunit;

public class PolluterRowBuilderTests
{
    [Fact]
    public void An_object_without_the_air_component_is_not_a_row()
    {
        Assert.Null(PolluterRowBuilder.BuildRow(new SteamTruckObject()));
    }

    [Fact]
    public void Reads_rate_total_owner_position_and_state()
    {
        var truck = new SteamTruckObject
        {
            Owners = new FakeAlias { Name = "Jovi", UserSet = { new FakeUser { Name = "Jovi" }, new FakeUser { Name = "Kuma" } } },
            Operating = true,
            Components = { new AirPollutionComponent { PollutionTonsPerHour = 0.5f, TotalTonsPollution = 3f } },
        };

        var row = PolluterRowBuilder.BuildRow(truck);

        Assert.NotNull(row);
        Assert.Equal("SteamTruckObject", row.Type);
        Assert.Equal("Jovi", row.Owner);
        Assert.Equal(new[] { "Jovi", "Kuma" }, row.OwnerUsers);
        Assert.Equal(0.5, row.RatePerHour);
        Assert.Equal(3.0, row.TotalTons);
        Assert.True(row.Operating);
        Assert.Equal(new PolluterPositionDto(1, 2, 3), row.Position);
    }

    [Fact]
    public void Owner_falls_back_to_the_creator_name()
    {
        var truck = new SteamTruckObject
        {
            NameOfCreator = "Netty",
            Components = { new AirPollutionComponent() },
        };

        Assert.Equal("Netty", PolluterRowBuilder.BuildRow(truck)?.Owner);
    }

    [Fact]
    public void Rich_text_tags_are_stripped_from_the_name()
    {
        var truck = new SteamTruckObject
        {
            FriendlyName = "<color=green>Steam</color> Truck",
            Components = { new AirPollutionComponent() },
        };

        Assert.Equal("Steam Truck", PolluterRowBuilder.BuildRow(truck)?.Name);
    }

    [Fact]
    public void Non_finite_rates_read_as_unknown()
    {
        var truck = new SteamTruckObject
        {
            Components = { new AirPollutionComponent { PollutionTonsPerHour = float.NaN, TotalTonsPollution = float.PositiveInfinity } },
        };

        var row = PolluterRowBuilder.BuildRow(truck);

        Assert.Null(row?.RatePerHour);
        Assert.Null(row?.TotalTons);
    }

    [Fact]
    public void Owner_members_are_capped_at_25()
    {
        var alias = new FakeAlias { Name = "Everyone" };
        for (var i = 0; i < 100; i++) alias.UserSet.Add(new FakeUser { Name = $"user{i}" });
        var truck = new SteamTruckObject { Owners = alias, Components = { new AirPollutionComponent() } };

        Assert.Equal(25, PolluterRowBuilder.BuildRow(truck)?.OwnerUsers.Length);
    }

    [Fact]
    public void A_throwing_component_still_yields_a_row_with_null_values()
    {
        var truck = new SteamTruckObject { Components = { new Throwing.AirPollutionComponent() } };

        var row = PolluterRowBuilder.BuildRow(truck);

        Assert.NotNull(row);
        Assert.Null(row.RatePerHour);
        Assert.Null(row.TotalTons);
    }
}
