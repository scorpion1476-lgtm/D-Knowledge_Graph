package Geometry::Shapes;

use strict;
use warnings;
use parent 'Geometry::Base';

sub new {
    my ($class, %args) = @_;
    return bless {%args}, $class;
}

sub area {
    my ($self) = @_;
    return 3.14159 * $self->{radius} * $self->{radius};
}

sub describe {
    my ($self) = @_;
    return sprintf("%f", $self->area());
}

1;
